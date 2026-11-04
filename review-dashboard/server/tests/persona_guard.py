"""Explicit selected-ledger dependencies and a guard against unmarked reads."""
import ast
import os
from pathlib import Path
import sys

_active=[]
_selected=None
_installed=False


def persona_sensitive(test):
    test.persona_sensitive=True
    return test


def is_persona_sensitive(test):
    module=sys.modules[type(test).__module__]
    return bool(getattr(module,'PERSONA_SENSITIVE',False) or getattr(type(test),'persona_sensitive',False) or getattr(getattr(test,test._testMethodName),'persona_sensitive',False))


def activate(test):
    _active.append((test.id(),is_persona_sensitive(test),[]))


def deactivate():
    return _active.pop()[2]


def _audit(event,args):
    if not _active or _active[-1][1] or not _selected or event not in ('open','os.listdir','os.scandir') or not args:return
    value=args[0]
    if not isinstance(value,(str,bytes,os.PathLike)):return
    path=Path(os.fsdecode(value)).absolute()
    if not path.is_relative_to(_selected):return
    # Every disposable Fixture uses this one fixed fictional policy, independently
    # of LEDGER_DIR. It is not a selection dependency.
    fixed=Path(__file__).resolve().parents[3]/'fixtures/ledgers/asha/AGENTS.md'
    if path==fixed:
        frame=sys._getframe(1)
        while frame:
            if frame.f_code.co_name=='setUp' and frame.f_code.co_filename==str(Path(__file__).with_name('support.py')):return
            frame=frame.f_back
    message=f'{_active[-1][0]} reads the selected ledger without @persona_sensitive or PERSONA_SENSITIVE = True: {path}'
    _active[-1][2].append(message)
    raise AssertionError(message)


def install(selected):
    global _selected,_installed
    _selected=Path(selected).resolve() if selected else None
    if not _installed:
        sys.addaudithook(_audit);_installed=True


def unmarked_reads(source,filename='<test>'):
    """Catch direct selection reads even when they do not open a ledger file.

    Filesystem auditing additionally catches selection hidden behind a helper.
    Environment dictionaries passed to disposable child processes are not reads.
    """
    tree=ast.parse(source,filename);errors=[];aliases={'getenv':'getenv','environ':'environ','ledger_root':'ledger_root'}
    for node in ast.walk(tree):
        if isinstance(node,ast.ImportFrom):
            for name in node.names:
                if name.name in aliases:aliases[name.asname or name.name]=name.name
    def marked(node):
        return any(isinstance(d,ast.Name) and d.id=='persona_sensitive' or isinstance(d,ast.Attribute) and d.attr=='persona_sensitive' for d in getattr(node,'decorator_list',[]))
    module_mark=any(isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='PERSONA_SENSITIVE' for t in n.targets) and isinstance(n.value,ast.Constant) and n.value.value is True for n in tree.body)
    def env(node):return isinstance(node,ast.Attribute) and node.attr=='environ' or isinstance(node,ast.Name) and aliases.get(node.id)=='environ'
    def visit(node,allowed=False):
        allowed=allowed or marked(node)
        read=False
        if isinstance(node,ast.Subscript) and isinstance(node.ctx,ast.Load) and env(node.value):read=isinstance(node.slice,ast.Constant) and node.slice.value in ('LEDGER_DIR','PERSONA')
        if isinstance(node,ast.Call):
            fn=node.func;name=fn.attr if isinstance(fn,ast.Attribute) else aliases.get(fn.id,fn.id) if isinstance(fn,ast.Name) else ''
            if name=='ledger_root':read=True
            if node.args and isinstance(node.args[0],ast.Constant) and node.args[0].value in ('LEDGER_DIR','PERSONA'):
                read=read or name=='getenv' or name in ('get','pop') and isinstance(fn,ast.Attribute) and env(fn.value)
        if read and not allowed:errors.append(f'{filename}:{node.lineno}: selected-persona read needs @persona_sensitive or PERSONA_SENSITIVE = True')
        for child in ast.iter_child_nodes(node):visit(child,allowed)
    visit(tree,module_mark)
    return errors


def check_marks(directory):
    errors=[error for path in sorted(Path(directory).glob('test_*.py')) for error in unmarked_reads(path.read_text(),str(path))]
    if errors:raise AssertionError('\n'.join(errors))
