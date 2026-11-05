"""The unmodified nine-column PlanGrid export is the browser input contract."""
import copy
import io
import json
import shutil
import unittest
from contextlib import redirect_stdout, redirect_stderr
from unittest.mock import MagicMock, patch

import test_preflight as fixtures
from test_automatic_migration import row
from kahua_migration.audit_report import render_audit_summary, write_audit_summary
from kahua_migration.browser import BrowserError
from kahua_migration.browser_migration import MigrationRunner, ProjectRunner
from kahua_migration.browser_navigation import DestinationNavigator, match_project
from kahua_migration.browser_setup import default_config
from kahua_migration.csv_config import merge_csv_config
from kahua_migration.journal import Journal
from kahua_migration.migration_cli import main
from kahua_migration.preflight import audit, verify_manifest


class ExportCsvTests(unittest.TestCase):
    def setUp(self):
        f = self.fixture = fixtures.MigrationTests()
        f.setUp()
        self.addCleanup(f.doCleanups)
        self.root = f.root
        self.clean_name = 'Project one'
        self.project = self.root / self.clean_name
        f.project.rename(self.project)
        (self.project / 'drawing.csv').rename(self.project / f'{self.clean_name}_drawing_log.csv')
        (self.project / 'current.csv').rename(self.project / f'{self.clean_name}_current_set_log.csv')
        self.versions = [dict(project_uid=v['project_uid'], project_name='Project/one',
                              project_clean_name=self.clean_name, project_kahua_entity_id='101',
                              project_sequence='2', version_set_uid=v['version_set_uid'],
                              version_name=v['version_name'], published_at=v['published_at'],
                              upload_sequence=v['upload_sequence']) for v in f.versions]
        self.config = default_config()
        self.config['source_root'] = '.'
        self.config['project_defaults']['target'].update(project_policy='existing_only', folder_policy='existing_only')
        self.save()

    def save(self):
        self.fixture.write_csv(self.root / 'version_sets.csv', self.versions)
        (self.root / 'config.json').write_text(json.dumps(self.config))

    def audit(self):
        self.save()
        return audit(self.root / 'version_sets.csv', self.root / 'config.json', source_root=self.root)

    def nav(self, target, journal):
        nav = DestinationNavigator(MagicMock(), journal, self.root / 'navigation', 'ocr')
        nav.target = copy.deepcopy(target)
        nav.guard = MagicMock()
        nav.evidence = MagicMock(return_value='observed.json')
        nav.find_project = MagicMock(return_value=row(1, '0008', self.clean_name))
        nav.create_project = MagicMock()
        return nav

    def journal(self):
        journal = Journal(self.root / 'journal.sqlite')
        self.addCleanup(journal.close)
        return journal

    def test_nine_columns_derive_prefixed_logs_folders_and_unique_name(self):
        self.assertEqual(len(self.versions[0]), 9)
        manifest = self.audit()
        self.assertEqual(manifest['summary']['errors'], 0, manifest['issues'])
        project = manifest['projects'][0]
        self.assertTrue(project['ready_for_upload'])
        self.assertEqual(project['source_directory'], self.clean_name)
        self.assertEqual([b['source_folder'] for b in project['batches']], ['Drawings/Set 1', 'Drawings/Set 2', 'Drawings/Set 3'])
        target = project['target']
        self.assertEqual(target['project_name'], self.clean_name)
        self.assertNotIn('project_number', target)
        self.assertEqual(target['project_lookup'], 'unique_name')
        self.assertEqual(target['export_project_entity_id'], '101')
        self.assertEqual(target['project_policy'], 'existing_only')
        self.assertEqual(target['folder_policy'], 'existing_only')
        self.assertEqual([r['revision'] for r in manifest['files'] if r['sheet_number'] == 'A'], ['1', '2', '3'])
        self.assertEqual(project['current_comparison']['identical_bytes'], 3)

    def test_blank_entity_ids_and_numeric_project_order_need_no_extra_columns(self):
        shutil.copytree(self.project, self.root / 'Project two')
        for suffix in ('drawing_log.csv', 'current_set_log.csv'):
            (self.root / 'Project two' / f'{self.clean_name}_{suffix}').rename(self.root / 'Project two' / f'Project two_{suffix}')
        self.versions += [dict(v, project_uid='p2', project_name='Project two', project_clean_name='Project two',
                               project_kahua_entity_id='', project_sequence='1', version_set_uid='p2-' + v['version_set_uid'])
                          for v in reversed(self.versions)]
        manifest = self.audit()
        self.assertEqual(manifest['summary']['errors'], 0, manifest['issues'])
        self.assertEqual([p['project_uid'] for p in manifest['projects']], ['p2', 'p1'])
        self.assertNotIn('export_project_entity_id', manifest['projects'][0]['target'])
        self.assertEqual([b['upload_sequence'] for b in manifest['projects'][0]['batches']], [1, 2, 3])

    def test_generic_log_fallback_and_ambiguous_pair(self):
        prefixed = self.project / f'{self.clean_name}_drawing_log.csv'
        generic = self.project / 'drawing_log.csv'
        shutil.copyfile(prefixed, generic)
        manifest = self.audit()
        self.assertIn('AMBIGUOUS_EXPORT_LOG', {i['code'] for i in manifest['issues']})
        self.assertFalse(manifest['projects'][0]['ready_for_upload'])
        prefixed.unlink()
        self.assertEqual(self.audit()['summary']['errors'], 0)

    def test_explicit_legacy_target_scope_is_unchanged(self):
        explicit = dict(self.config['project_defaults']['target'], project_number='0008', project_name='Mapped project')
        self.config['projects'] = [dict(project_uid='p1', target=explicit,
                                       source_directory=self.clean_name,
                                       drawing_log=f'{self.clean_name}_drawing_log.csv',
                                       current_log=f'{self.clean_name}_current_set_log.csv')]
        manifest = self.audit()
        self.assertEqual(manifest['summary']['errors'], 0)
        self.assertEqual(manifest['projects'][0]['target'], explicit)

    def test_zip_log_prefix_can_differ_from_export_directory_only_when_unique(self):
        for suffix in ('drawing_log.csv', 'current_set_log.csv'):
            (self.project / f'{self.clean_name}_{suffix}').rename(self.project / f'Original-name_{suffix}')
        self.assertEqual(self.audit()['summary']['errors'], 0)
        shutil.copyfile(self.project / 'Original-name_drawing_log.csv', self.project / 'Another_drawing_log.csv')
        manifest = self.audit()
        self.assertFalse(manifest['projects'][0]['ready_for_upload'])
        self.assertIn('AMBIGUOUS_EXPORT_LOG', {i['code'] for i in manifest['issues']})

    def test_missing_folder_reports_resolved_path(self):
        shutil.rmtree(self.project / 'Drawings/Set 2')
        manifest = self.audit()
        issue = next(i for i in manifest['issues'] if i['code'] == 'VERSION_FOLDER_UNAVAILABLE')
        self.assertIn(str((self.project / 'Drawings/Set 2').resolve()), issue['message'])
        self.assertEqual(issue['version_set_uid'], 'v2')

    def test_blank_titles_use_sheet_numbers_with_provenance_and_current_comparison(self):
        for record in self.fixture.rows:
            if record['Sheet Name'] == 'A':
                record['Description'] = ' ' if record['Version'] == 'Set 2' else ''
        self.fixture.write_csv(self.project / f'{self.clean_name}_drawing_log.csv', self.fixture.rows)
        self.fixture.write_csv(self.project / f'{self.clean_name}_current_set_log.csv',
                               [r for r, _ in self.fixture.latest.values()])
        manifest = self.audit()
        self.assertEqual(manifest['summary']['errors'], 0, manifest['issues'])
        self.assertTrue(manifest['projects'][0]['ready_for_upload'])
        fallback = [r for r in manifest['files'] if r['sheet_number'] == 'A']
        self.assertEqual([r['title'] for r in fallback], ['A', 'A', 'A'])
        self.assertEqual([r['source_title'] for r in fallback], ['', ' ', ''])
        self.assertTrue(all(r['title_source'] == 'sheet_number' for r in fallback))
        self.assertEqual(sum(i['code'] == 'TITLE_FROM_SHEET_NUMBER' and i['severity'] == 'warning'
                             for i in manifest['issues']), 3)
        self.assertEqual(next(r['title'] for r in manifest['files'] if r['sheet_number'] == 'B'), 'Title B')
        self.fixture.latest['A'][0]['Description'] = 'Different current title'
        self.fixture.write_csv(self.project / f'{self.clean_name}_current_set_log.csv',
                               [r for r, _ in self.fixture.latest.values()])
        self.assertIn('CURRENT_METADATA_MISMATCH', {i['code'] for i in self.audit()['issues']})

    def test_unsafe_and_inconsistent_export_names_block_only_the_project(self):
        for bad in ('../outside', 'C:\\outside', '.', 'Project/one'):
            with self.subTest(bad=bad):
                rows = [dict(v, project_clean_name=bad) for v in self.versions]
                project = merge_csv_config(self.config, rows, source_root=self.root)['projects'][0]
                self.assertEqual(project['_export_issues'][0]['code'], 'INVALID_EXPORT_MAPPING')
        self.versions[1]['project_clean_name'] = 'Different'
        self.assertIn('INVALID_EXPORT_MAPPING', {i['code'] for i in self.audit()['issues']})

    def test_two_project_uids_cannot_share_an_inferred_source_directory(self):
        self.versions += [dict(v, project_uid='p2', project_sequence='1') for v in self.versions]
        manifest = self.audit()
        self.assertEqual({i['project_uid'] for i in manifest['issues'] if i['code'] == 'SOURCE_DIRECTORY_COLLISION'}, {'p1', 'p2'})
        self.assertTrue(all(not p['ready_for_upload'] for p in manifest['projects']))

    def test_exact_name_matching_rejects_ambiguous_numbers_and_near_names(self):
        self.assertIsNone(match_project([row(1, '0008', 'Project one extra')], None, self.clean_name))
        self.assertEqual(match_project([row(1, '0008', self.clean_name)], None, self.clean_name)['grid_row'], 1)
        with self.assertRaisesRegex(BrowserError, 'More than one'):
            match_project([row(1, '0008', self.clean_name), row(2, '0009', self.clean_name)], None, self.clean_name)

    def test_resolved_project_identity_is_pinned_across_resume(self):
        target = self.audit()['projects'][0]['target']
        journal = self.journal()
        nav = self.nav(target, journal)
        nav.ensure_project()
        self.assertEqual(nav.target['project_number'], '0008')
        nav2 = self.nav(target, journal)
        nav2.ensure_project()
        nav2.find_project.return_value = row(1, '0009', self.clean_name)
        with self.assertRaisesRegex(BrowserError, 'differs from the identity'):
            nav2.ensure_project()
        nav2.create_project.assert_not_called()
        self.assertNotIn('project_number', target)

    def test_nonblank_entity_id_prevents_creating_a_replacement(self):
        target = dict(self.audit()['projects'][0]['target'], project_policy='find_or_create')
        nav = self.nav(target, self.journal())
        nav.find_project.return_value = None
        with self.assertRaisesRegex(BrowserError, 'refusing to create a replacement'):
            nav.ensure_project()
        nav.create_project.assert_not_called()

    def test_pending_creation_checks_intended_number_and_never_repeats_save(self):
        for v in self.versions:
            v['project_kahua_entity_id'] = ''
        target = dict(self.audit()['projects'][0]['target'], project_policy='find_or_create')
        journal = self.journal()
        nav = self.nav(target, journal)
        nav.find_project.side_effect = [None, row(1, '0099', self.clean_name)]
        nav.create_project.side_effect = lambda key, spec: journal.begin_destination(key, spec, {'intended_number': '0008', 'evidence_path': 'precommit.json'})
        with self.assertRaisesRegex(BrowserError, 'differs from the identity'):
            nav.ensure_project()
        nav2 = self.nav(target, journal)
        nav2.find_project.return_value = None
        with self.assertRaisesRegex(BrowserError, 'refusing to repeat Save'):
            nav2.ensure_project()
        nav2.create_project.assert_not_called()

    def test_runtime_binding_preserves_manifest_and_operation_journal(self):
        manifest = self.audit()
        original = copy.deepcopy(manifest)
        target = manifest['projects'][0]['target']
        resolved = dict(target, project_number='0008')
        journal = self.journal()
        nav = MagicMock()
        nav.return_value.ensure.return_value = {'target': resolved}
        project_factory = MagicMock()
        project_factory.return_value.run.return_value = {'project_uid': 'p1', 'passed': True}
        result = MigrationRunner(MagicMock(), manifest, journal, self.root / 'queue', 'ocr',
                                 navigator_factory=nav, project_factory=project_factory,
                                 session_recovery=MagicMock()).run()
        self.assertTrue(result['passed'])
        self.assertEqual(project_factory.call_args.kwargs['resolved_target'], resolved)
        with patch('kahua_migration.browser_migration.RecordFilterInspector'):
            runner = ProjectRunner(MagicMock(), manifest, journal, self.root / 'run', 'ocr', 'p1', resolved_target=resolved)
        runner.inspector = MagicMock()
        runner._batch = MagicMock(side_effect=BrowserError('stop after registering'))
        result = runner.run()
        self.assertIn('stop after registering', result['error'])
        self.assertEqual(runner.project['target']['project_number'], '0008')
        self.assertEqual(manifest, original)
        verify_manifest(manifest)
        with self.assertRaisesRegex(BrowserError, 'differs from the audited'):
            ProjectRunner(MagicMock(), manifest, journal, self.root / 'bad', 'ocr', 'p1',
                          resolved_target=dict(resolved, domain_name='Wrong domain'))

    def test_navigation_does_not_mutate_the_audited_request(self):
        target = self.audit()['projects'][0]['target']
        nav = self.nav(target, self.journal())
        nav.ensure_domain = MagicMock()
        nav.ensure_folders = MagicMock()
        result = nav.ensure(target)
        self.assertEqual(result['target']['project_number'], '0008')
        self.assertNotIn('project_number', target)

    def test_failed_plan_and_execute_print_actionable_audit_without_browser(self):
        (self.project / 'Drawings/Set 2/A.pdf').unlink()
        for execute in (False, True):
            output, errors = io.StringIO(), io.StringIO()
            report_dir = self.root / ('execute-report' if execute else 'plan-report')
            with redirect_stdout(output), redirect_stderr(errors), \
                    patch('kahua_migration.migration_cli.resolve_ocr') as ocr, \
                    patch('playwright.sync_api.sync_playwright') as browser:
                code = main(['--config', str(self.root / 'config.json'), '--source-root', str(self.root),
                             '--journal', str(self.root / 'never.sqlite'), '--out', str(report_dir)] +
                            (['--execute'] if execute else []))
            self.assertEqual(code, 1 if execute else 2)
            text = output.getvalue()
            self.assertIn('BLOCKED: Project/one', text)
            self.assertIn('[INVALID_DRAWING]', text)
            self.assertIn("version 'Set 2'", text)
            self.assertIn("sheet 'A'", text)
            self.assertIn('Fix:', text)
            self.assertIn('audit-summary.txt', text)
            self.assertTrue((report_dir / 'audit-summary.txt').exists())
            browser.assert_not_called()
            ocr.assert_not_called()
            self.assertFalse((self.root / 'never.sqlite').exists())
            if execute:
                self.assertIn('No browser opened', errors.getvalue())

    def test_full_log_keeps_issues_beyond_console_cap_and_filters_projects(self):
        manifest = self.audit()
        for i in range(25):
            manifest['issues'].append(dict(severity='warning', code='SAMPLE_WARNING', project_uid='p1',
                                          message=f'Warning {i}', sheet_number=str(i)))
        manifest['projects'].append(dict(manifest['projects'][0], project_uid='p2', project_name='Unselected'))
        output = io.StringIO()
        with redirect_stdout(output):
            path = write_audit_summary(manifest, self.root / 'reports', ['p1'])
        self.assertIn('5 more issues', output.getvalue())
        self.assertIn('WARNING [SAMPLE_WARNING]: 25 issue(s)', output.getvalue())
        self.assertIn('Warning 24', path.read_text())
        self.assertNotIn('Unselected', path.read_text())
        self.assertIn('READY:', render_audit_summary(manifest, ['p1']))


if __name__ == '__main__':
    unittest.main()
