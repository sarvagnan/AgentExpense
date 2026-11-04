"""Selected-ledger reads require explicit marks; independent fixtures stay once."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import persona_guard as guard


class PersonaGuardTests(unittest.TestCase):
    def test_selection_reads_require_marks_and_disposable_environment_writes_do_not(self):
        for statement in ["os.environ['LEDGER_DIR']", "os.environ.get('LEDGER_DIR')", "os.getenv('PERSONA')", "ledger_root()", "from os import getenv as read\nread('LEDGER_DIR')"]:
            with self.subTest(statement=statement):
                self.assertTrue(guard.unmarked_reads(statement))
                self.assertEqual(guard.unmarked_reads('PERSONA_SENSITIVE = True\n'+statement),[])
        self.assertEqual(guard.unmarked_reads("env={**os.environ,'LEDGER_DIR':str(self.ledger)}\nself.assertEqual(env['LEDGER_DIR'],str(self.ledger))"),[])
        self.assertEqual(guard.unmarked_reads("@persona_sensitive\ndef test_currency():\n return os.getenv('LEDGER_DIR')"),[])

    def test_indirect_file_read_fails_without_mark_and_marked_read_is_allowed(self):
        with tempfile.TemporaryDirectory() as folder,patch.object(guard,'_selected',Path(folder)),patch.object(guard,'_active',[]):
            path=Path(folder)/'ledger.json';path.write_text('Fictional selected ledger')
            for marked in (False,True):
                guard._active.append(('fictional.test',marked,[]))
                try:
                    if marked:self.assertIsNone(guard._audit('open',(str(path),'r',0)))
                    else:
                        with self.assertRaisesRegex(AssertionError,'without @persona_sensitive'):guard._audit('open',(str(path),'r',0))
                finally:
                    violations=guard.deactivate()
                    self.assertEqual(bool(violations),not marked,'A caught read failure must still fail the inventory guard')

    def test_flags_class_decorators_and_method_decorators_select_exact_cases(self):
        @guard.persona_sensitive
        class Marked(unittest.TestCase):
            def runTest(self):pass
        class Mixed(unittest.TestCase):
            @guard.persona_sensitive
            def test_selected(self):pass
            def test_independent(self):pass
        self.assertTrue(guard.is_persona_sensitive(Marked()))
        self.assertTrue(guard.is_persona_sensitive(Mixed('test_selected')))
        self.assertFalse(guard.is_persona_sensitive(Mixed('test_independent')))

    def test_repository_marks_cover_direct_selection_reads(self):
        guard.check_marks(Path(__file__).parent)
