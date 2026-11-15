"""Local controller identity, detached launch and atomic progress records."""
from functools import lru_cache
import hashlib
import errno
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import threading


@lru_cache(maxsize=1)
def host_identity():
    """Hashed Darwin hardware or Linux boot/namespace identity; never a hostname."""
    try:
        if sys.platform == 'darwin':
            result = subprocess.run(['ioreg','-rd1','-c','IOPlatformExpertDevice'],
                                    text=True,capture_output=True,timeout=5)
            match = re.search(r'"IOPlatformUUID"\s*=\s*"([^"]+)"', result.stdout)
            value = match.group(1) if match else None
        else:
            value='\0'.join((Path('/etc/machine-id').read_text().strip(),
                             Path('/proc/sys/kernel/random/boot_id').read_text().strip(),
                             os.readlink('/proc/self/ns/pid')))
        return hashlib.sha256(value.encode()).hexdigest() if value else None
    except (OSError, subprocess.SubprocessError):
        return None


def process_identity(pid):
    """None means uncertain, '' means absent/zombie; otherwise a birth timestamp."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return ''
    except PermissionError:
        return None
    if sys.platform.startswith('linux'):
        try:
            fields = (Path('/proc')/str(pid)/'stat').read_text().rpartition(') ')[2].split()
        except FileNotFoundError:
            return ''
        except OSError:
            return None
        try:
            if fields[0] == 'Z':
                return ''
            boot = Path('/proc/sys/kernel/random/boot_id').read_text().strip()
            return boot+':'+fields[19] if boot else None
        except (OSError, IndexError):
            return None
    if sys.platform == 'darwin':
        # proc_bsdinfo from Darwin's libproc: start seconds + microseconds.
        # This reads only the tagged PID, rather than inventorying processes.
        import ctypes
        class BSDInfo(ctypes.Structure):
            _fields_ = [('head',ctypes.c_uint32*12),('comm',ctypes.c_char*16),
                        ('name',ctypes.c_char*32),('tail',ctypes.c_uint32*6),
                        ('seconds',ctypes.c_uint64),('microseconds',ctypes.c_uint64)]
        try:
            library=ctypes.CDLL('/usr/lib/libproc.dylib',use_errno=True)
            library.proc_pidinfo.argtypes=[ctypes.c_int,ctypes.c_int,ctypes.c_uint64,ctypes.c_void_p,ctypes.c_int]
            library.proc_pidinfo.restype=ctypes.c_int
            info=BSDInfo()
            ctypes.set_errno(0)
            size=library.proc_pidinfo(pid,3,0,ctypes.byref(info),ctypes.sizeof(info))
            if size != ctypes.sizeof(info):
                return '' if ctypes.get_errno()==errno.ESRCH else None
            if info.head[3] != pid or not info.seconds:
                return None
            return '' if info.head[1]==5 else f'{info.seconds}:{info.microseconds}'
        except (OSError,AttributeError):
            return None
    try:
        result = subprocess.run(['ps','-p',str(pid),'-o','lstart=','-o','stat='],
                                text=True,capture_output=True,timeout=5)
        if result.returncode == 1 and not result.stdout.strip():
            return ''
        parts = result.stdout.split()
        if result.returncode or len(parts) != 6:
            return None
        return '' if parts[-1].startswith('Z') else ' '.join(parts[:5])
    except (OSError, subprocess.SubprocessError):
        return None


def owner_record(token):
    return {'version':1,'token':token,'host':host_identity(),
            'pid':os.getpid(),'birth':process_identity(os.getpid())}


def owner_status(record):
    if (not isinstance(record,dict) or record.get('version') != 1
            or not re.fullmatch(r'[0-9a-f]{32}', str(record.get('token','')))
            or not record.get('host') or record.get('host') != host_identity()
            or type(record.get('pid')) is not int or not 1 < record['pid'] <= 2147483647
            or not isinstance(record.get('birth'),str) or not record['birth']):
        return 'unknown'
    birth = process_identity(record['pid'])
    if birth is None:
        return 'unknown'
    return 'alive' if birth == record['birth'] else 'dead'


def atomic_json(path, value):
    temporary = path.with_suffix(path.suffix+'.next')
    temporary.write_text(json.dumps(value,indent=2)+'\n')
    temporary.replace(path)


def spawn_controller(output, command, *, environment=None):
    with (output/'runner.log').open('a') as log:
        child = subprocess.Popen(command,stdin=subprocess.DEVNULL,stdout=log,stderr=subprocess.STDOUT,
                                 start_new_session=True,close_fds=True,env=environment)
    atomic_json(output/'launcher.json',{'pid':child.pid})
    return child


class ControllerState:
    def __init__(self, output, controller):
        self.output = output
        self.lock = threading.Lock()
        self.data = {'status':'running','controller':controller,'pid':os.getpid(),'instances':{}}
        (output/'runner.pid').write_text(str(os.getpid())+'\n')
        self.update()

    def update(self, **values):
        with self.lock:
            self.data.update(values)
            atomic_json(self.output/'runner.json',self.data)

    def instance(self, name, details=None):
        with self.lock:
            if details is None:
                self.data['instances'].pop(name,None)
            else:
                self.data['instances'][name]=details
            atomic_json(self.output/'runner.json',self.data)
