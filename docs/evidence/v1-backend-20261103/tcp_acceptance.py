"""Loopback TCP FastAPI/SQLite acceptance; all workbooks and identities are synthetic."""
import importlib.util
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
from collections import Counter
from fractions import Fraction
from io import BytesIO

import httpx
from fastapi.routing import iter_route_contexts
from openpyxl import load_workbook

from app.core.config import Settings
from app.edu.auth import TeacherAdmin
from app.importing.edu.types import DIMENSIONS
from app.main import create_app
from app.storage.database import Database
from app.storage.request_traces import RequestTraceStore

ROOT = Path('D:/AI/传智杯')
WORK = ROOT / '.task-cache/backend-v1-integration-20261103'
API = WORK / 'apps/api'
OUT = Path(__file__).resolve().parent
TEMP = ROOT / '.task-cache/joint-browser-temp'
SAFE = 2**53 - 1
checks = []
requests = []
proof = {'synthetic_only': True, 'transport': 'httpx -> loopback TCP -> uvicorn/FastAPI -> temporary SQLite', 'status': 'running'}

def mark(name):
    checks.append(name)

def unsafe(value):
    if type(value) is int:
        return int(abs(value) > SAFE)
    if isinstance(value, dict):
        return sum(unsafe(v) for v in value.values())
    if isinstance(value, list):
        return sum(unsafe(v) for v in value)
    return 0

class CheckedTemporaryDirectory(tempfile.TemporaryDirectory):
    def cleanup(self):
        assert Path(self.name).resolve().is_relative_to(TEMP.resolve())
        assert Path(self.name).name.startswith('weiran-backend-v1-')
        return super().cleanup()

fixture_spec = importlib.util.spec_from_file_location('synthetic_fixture', ROOT / '.task-cache/p0-20261101/linming/make-discovery-fixtures.py')
fixture = importlib.util.module_from_spec(fixture_spec)
fixture_spec.loader.exec_module(fixture)

with CheckedTemporaryDirectory(prefix='weiran-backend-v1-', dir=TEMP) as folder:
    private = Path(folder)
    database = Database(private / 'synthetic.sqlite3')
    database.migrate()
    traces = RequestTraceStore(private / 'traces.sqlite3')
    traces.initialize()
    admin = TeacherAdmin(database)
    teacher = admin.create_teacher('synthetic-integration-owner')
    reader = admin.create_teacher('synthetic-integration-readonly')
    scope = admin.create_scope('合成后端集成验收')
    admin.set_member(teacher, scope, can_import=True)
    admin.set_member(reader, scope, can_import=False)
    owner_token = admin.issue_token(teacher, hours=1)
    reader_token = admin.issue_token(reader, hours=1)
    application = create_app(Settings(environment='test', database_path=database.path))
    registered = []
    operation_ids = []
    for route in iter_route_contexts(application.routes):
        if (getattr(route, 'path', None) or '').startswith('/api/'):
            registered.extend((method, route.path) for method in getattr(route, 'methods', ()))
            if getattr(route, 'unique_id', None):
                operation_ids.append(route.unique_id)
    assert len(registered) > 20, 'Inspect resolved FastAPI route contexts, not lazy included-router placeholders'
    assert all(count == 1 for count in Counter(registered).values())
    assert len(operation_ids) == len(set(operation_ids))
    proof['registered_api_operations'] = len(registered)
    mark('No duplicate method/path registrations or FastAPI operation IDs')

    # Prevent accidental non-loopback connections in server and spawned children.
    guard = private / 'guard'
    guard.mkdir()
    (guard / 'sitecustomize.py').write_text('''import socket
_connect = socket.socket.connect
_connect_ex = socket.socket.connect_ex
def check(address):
    if not isinstance(address, tuple) or address[0] not in {'127.0.0.1', '::1'}:
        raise RuntimeError('OFFLINE_ACCEPTANCE_EXTERNAL_NETWORK_DENIED')
def connect(sock, address):
    check(address)
    return _connect(sock, address)
def connect_ex(sock, address):
    check(address)
    return _connect_ex(sock, address)
socket.socket.connect = connect
socket.socket.connect_ex = connect_ex
''', encoding='utf-8')
    env = os.environ.copy()
    for key in list(env):
        if 'QWEN' in key.upper() or 'DASHSCOPE' in key.upper() or key.upper().startswith('OPENAI_'):
            env.pop(key, None)
    env.update(PYTHONPATH=str(guard) + os.pathsep + str(API), PYTHONUTF8='1', PYTHONIOENCODING='utf-8',
               WEIRAN_EDU_ENV='test', WEIRAN_EDU_DATABASE=str(database.path),
               WEIRAN_EDU_REQUEST_TRACE_DATABASE=str(private / 'traces.sqlite3'))
    with socket.socket() as listener:
        listener.bind(('127.0.0.1', 0))
        port = listener.getsockname()[1]
    server_log = (OUT / 'tcp-server.log').open('wb')
    server = subprocess.Popen([sys.executable, '-m', 'uvicorn', 'app.main:create_app', '--factory',
                               '--host', '127.0.0.1', '--port', str(port), '--no-access-log'],
                              cwd=API, env=env, stdout=server_log, stderr=subprocess.STDOUT,
                              creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
    try:
        with httpx.Client(base_url=f'http://127.0.0.1:{port}', timeout=20, trust_env=False) as client:
            deadline = time.monotonic() + 60
            while True:
                assert server.poll() is None, 'Read tcp-server.log: server exited during startup'
                try:
                    ready = client.get('/openapi.json')
                    if ready.status_code == 200:
                        break
                except httpx.TransportError:
                    pass
                assert time.monotonic() < deadline, 'Server startup failed'
                time.sleep(0.1)

            def request(method, path, expected=200, token=None, **kwargs):
                credential = owner_token if token is None else token
                headers = {'Authorization': 'Bearer ' + credential} if credential else {}
                response = client.request(method, '/api/v1/edu' + path, headers=headers, **kwargs)
                requests.append({'method': method, 'path': path, 'status': response.status_code, 'expected': expected,
                                 'request_id': response.headers.get('X-Request-ID')})
                assert response.status_code == expected, (method, path, response.status_code, expected)
                assert response.headers.get('X-Request-ID', '').startswith('req_')
                assert response.headers.get('Cache-Control') == 'no-store'
                return response

            def upload(kind, size):
                fixture.make(private, kind)
                raw = (private / (kind + '.xlsx')).read_bytes()
                response = request('POST', '/imports/preview', 201,
                                   files={'file': (kind + '.xlsx', raw, 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')})
                view = response.json()
                assert view['import']['title'] == kind
                assert (view['summary']['detected_student_count'], view['summary']['parsed_evaluation_count'], view['summary']['error_count']) == (size, size * 5, 0)
                assert len(view['recognized_items']) == 5
                assert all(item['score_row_count'] == size for item in view['recognized_items'])
                return view, raw

            def commit(view, expected=201):
                return request('POST', '/imports/' + view['import']['import_id'] + '/commit', expected,
                               json={'preview_version': view['import']['preview_version'], 'acknowledge_warnings': True}).json()

            view, raw = upload('precision105', 105)
            import_path = '/imports/' + view['import']['import_id']
            quality = request('GET', import_path + '/quality-report').json()
            assert quality['validation_passed'] and quality['requires_warning_acknowledgement']
            assert any(g['code'] == 'COMMENT_REPEATED' and g['occurrence_count'] > 0 for g in quality['issue_groups'])
            assert sum(g['occurrence_count'] for g in quality['issue_groups'] if g['severity'] == 'warning') == view['summary']['warning_count']
            limited = request('GET', import_path, params={'issue_limit': 1, 'row_limit': 1}).json()
            assert limited['summary'] == view['summary'] and limited['recognized_items'] == view['recognized_items']
            renamed = request('PATCH', import_path, json={'preview_version': view['import']['preview_version'], 'title': '合成105人正式后端验收'}).json()
            request('POST', import_path + '/commit', 409, json={'preview_version': view['import']['preview_version'], 'acknowledge_warnings': True})
            request('POST', import_path + '/commit', 409, json={'preview_version': renamed['import']['preview_version']})
            created = commit(renamed)
            assert created['result'] == 'created'
            base = '/batches/' + created['batch_id']
            spec = {'snapshot_id': created['snapshot_id']}
            batch = request('GET', base).json()
            assert batch['student_count'] == 105 and batch['evaluation_count'] == 525
            assert batch['snapshot_id'] == created['snapshot_id']
            replayed = commit(renamed, 200)
            assert replayed['result'] == 'replayed' and replayed['snapshot_id'] == created['snapshot_id']
            repeated = request('POST', '/imports/preview', 201, files={'file': ('precision105.xlsx', raw, 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')}).json()
            duplicate = commit(repeated, 200)
            assert duplicate['result'] == 'duplicate' and duplicate['batch_id'] == created['batch_id'] and duplicate['snapshot_id'] == created['snapshot_id']
            mark('File-only upload, structured quality totals, rename, stale preview, warning acknowledgement, created/duplicate/replayed references')

            statistics = request('GET', base + '/statistics', params=spec).json()
            book = load_workbook(BytesIO(raw), data_only=True)
            values = {d.code: [] for d in DIMENSIONS}
            labels = {d.label: d.code for d in DIMENSIONS}
            for row in book['Sheet2'].values:
                if len(row) >= 3 and row[1] in labels:
                    values[labels[row[1]]].append(Fraction(str(row[2])))
            book.close()
            for dimension in statistics['dimensions']:
                expected_values = values[dimension['dimension_code']]
                assert len(expected_values) == dimension['valid_count'] == 105
                exact = dimension['mean']['exact']
                assert Fraction(int(exact['numerator']), int(exact['denominator'])) == sum(expected_values, Fraction()) / 105
            listed = request('POST', base + '/discoveries/query', json=spec | {'limit': 50}).json()
            assert listed['scope']['statistics'] == statistics
            assert listed['scope']['scope_record_count'] == 105
            page = request('POST', base + '/discoveries/query', json=spec | {'offset': 1, 'limit': 1}).json()
            assert page['scope'] == listed['scope'] and page['items'] == listed['items'][1:2]
            card = next(item for item in listed['items'] if item['type'] == 'dimension_relation')
            path = base + '/discoveries/' + card['discovery_id']
            detail_response = request('POST', path + '/query', json=spec)
            detail = detail_response.json()
            assert detail['ranking']['score'] == {'numerator': '24840307107174983', 'denominator': '25000000000000000', 'display': '0.9936'}
            assert not unsafe(detail) and detail['ranking']['is_statistical_confidence'] is False
            assert '"24840307107174983"' in detail_response.text
            (OUT / 'tcp-discovery.synthetic.json').write_bytes(detail_response.content)
            chart = request('POST', path + '/chart/query', json=spec).json()
            assert chart['chart'] == detail['chart'] and chart['metrics'] == detail['metrics']
            records = request('POST', path + '/records/query', json=spec | {'metric_key': detail['controlled_queries'][0]['metric_key'], 'limit': 2}).json()
            assert records['total_count'] == 105
            for student in records['items']:
                for score in student['scores']:
                    evidence = request('GET', base + '/evidence/' + score['evidence_id'], params=spec).json()
                    assert (evidence['student_id'], evidence['dimension_code'], evidence['score']) == (student['student_id'], score['dimension_code'], score['score'])
                    assert evidence['comment'] is None
            request('POST', path + '/query', 409, json={'snapshot_id': 'stale-synthetic-snapshot'})
            mark('105/525 exact statistics independently reconciled, pagination invariant, lossless ranking, real chart and authorized score provenance')

            save_body = spec | {'conditions': [], 'include_dimensions': False, 'title': '合成教师确认分析',
                                'discovery': {'input_key': listed['scope']['input_key'], 'discovery_ids': [card['discovery_id']]}}
            saved = request('POST', base + '/analysis-results', 201, json=save_body).json()
            result_path = base + '/analysis-results/' + saved['result_id']
            assert saved['analysis']['discovery']['findings'][0] == saved['discovery_findings'][0]['discovery'] == detail
            assert request('GET', result_path).json() == saved
            patch_body = spec | {'expected_version': 1, 'decisions': [{'candidate_id': card['discovery_id'], 'status': 'confirmed'}]}
            reviewed = request('PATCH', result_path, json=patch_body).json()
            assert reviewed['version'] == 2 and reviewed['analysis'] == saved['analysis']
            assert request('GET', result_path).json() == reviewed
            assert reviewed['discovery_findings'][0]['teacher_review']['status'] == 'confirmed'
            request('PATCH', result_path, 409, json=patch_body)
            for kind in ('md', 'xlsx'):
                export_spec = spec | {'expected_version': 2, 'format': kind}
                request('POST', result_path + '/export-check', json=export_spec)
                report = request('GET', result_path + '/export', params=export_spec)
                assert 'attachment;' in report.headers['Content-Disposition']
                (OUT / ('tcp-report.synthetic.' + kind)).write_bytes(report.content)
                if kind == 'md':
                    assert '教师确认发现' in report.text
                    assert all(reference in report.text for reference in (created['batch_id'], created['snapshot_id'], saved['result_id'], card['discovery_id']))
                    assert '未审核合成私密评语' not in report.text and '合成私密姓名' not in report.text
                else:
                    workbook = load_workbook(BytesIO(report.content))
                    try:
                        overview = {row[0]: row[1] for row in workbook['测评概况'].values if len(row) > 1 and row[0] is not None}
                        assert overview['测评记录数'] == 105 and overview['教师已确认发现数'] == 1
                        audit = {row[0]: row[1] for row in workbook['技术附录'].values if len(row) > 1 and row[0] is not None}
                        assert audit['测评批次引用'] == created['batch_id'] and audit['数据快照引用'] == created['snapshot_id']
                        assert audit['保存结果引用'] == saved['result_id'] and audit['教师核验版本'] == 2
                        for row in workbook['维度统计'].values:
                            if row[0] in labels:
                                expected_values = values[labels[row[0]]]
                                assert row[1] == 105 and abs(row[2] - float(sum(expected_values, Fraction()) / 105)) < 1e-12
                        assert all(cell.data_type != 'f' for sheet in workbook for row in sheet for cell in row)
                    finally:
                        workbook.close()
            request('GET', result_path + '/export', 409, params=spec | {'expected_version': 1, 'format': 'md'})
            mark('Save/reopen both nested discoveries, teacher confirmed version2, stale-version rejection and actual MD/XLSX download/reopen with matching facts/references')

            admin.grant_read(reader, created['batch_id'])
            request('GET', result_path, token=reader_token)
            request('POST', base + '/discoveries/query', token=reader_token, json=spec)
            request('POST', base + '/analysis-results', 403, token=reader_token, json=save_body)
            request('PATCH', result_path, 403, token=reader_token, json=spec | {'expected_version': 2, 'note': '合成只读不可写'})
            admin.revoke_read(reader, created['batch_id'])
            request('GET', result_path, 404, token=reader_token)
            request('POST', base + '/discoveries/query', 404, token=reader_token, json=spec)
            request('GET', result_path + '/export', 404, token=reader_token, params=spec | {'expected_version': 2, 'format': 'md'})
            request('POST', base + '/discoveries/query', 401, token='', content=b'not-json')
            mark('Readonly access/write denial, revocation on subsequent queries/download and authentication before JSON parsing')

            empty_view, _ = upload('constant', 17)
            empty_batch = commit(empty_view)
            empty_base = '/batches/' + empty_batch['batch_id']
            empty_spec = {'snapshot_id': empty_batch['snapshot_id']}
            empty = request('POST', empty_base + '/discoveries/query', json=empty_spec).json()
            assert empty['items'] == [] and empty['retained_count'] == 0
            assert empty['scope']['scope_record_count'] == 17
            assert empty['scope']['statistics']['dimensions'][0]['valid_count'] == 17
            assert request('GET', base + '/statistics', params=spec).json() == statistics
            request('POST', path + '/query', 409, json=empty_spec)
            mark('17-person zero findings, new/old batch isolation and cross-snapshot rejection')

            normal_view, _ = upload('contrast', 53)
            normal_batch = commit(normal_view)
            normal_base = '/batches/' + normal_batch['batch_id']
            normal = request('POST', normal_base + '/discoveries/query', json={'snapshot_id': normal_batch['snapshot_id']}).json()
            assert normal['items'] and normal['scope']['scope_record_count'] == 53
            assert all(d['valid_count'] == 53 for d in normal['scope']['statistics']['dimensions'])
            mark('53-person normal import/diagnosis remains compatible')
            last_request = requests[-1]['request_id']
            request('GET', '/diagnostics/requests/' + last_request)
            request('GET', '/diagnostics/requests/' + last_request, 404, token=reader_token)
            mark('Operator-initialized dedicated request trace store and owner-only diagnostic lookup')
            proof.update(status='passed', checks=checks, request_count=len(requests), requests=requests,
                         students=105, scores=525, exact_ranking=detail['ranking']['score'], zero_findings_students=17,
                         normal_students=53, registered_api_operations=len(registered), unsafe_json_integers=0,
                         outbound_network='Server and spawned workers limited to loopback by test-only guard', browser_executed=False)
    finally:
        server.terminate()
        try:
            server.wait(timeout=10)
        except subprocess.TimeoutExpired:
            server.kill()
            server.wait(timeout=5)
        server_log.close()
        proof.update(checks=checks, request_count=len(requests), requests=requests)
        (OUT / 'tcp-summary.json').write_text(json.dumps(proof, ensure_ascii=False, indent=2), encoding='utf-8')
assert proof['status'] == 'passed'
print(json.dumps({k: v for k, v in proof.items() if k != 'requests'}, ensure_ascii=False))
