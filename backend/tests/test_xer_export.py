"""P6 XER export, checked against a real P6 file and a real simulation.

Two independent things are proven here, and it is worth keeping them apart:

1. **The structure matches a real P6 export.** Every column list in the exporter is compared,
   column for column and in order, against `samples/reference.xer` - a public P6 5.0 export.
   Not against my recollection of the format.

2. **The content survives a round trip.** A REAL simulation is run end to end, exported, and
   read back with `xer-reader`, a third-party parser this project does not control. Activity
   count, every date, every duration and every dependency are compared against the
   SimulationOutput they came from.

The simulation is real on purpose. A hand-built fixture would be a fixture designed to pass:
the previous attempt at this exporter had twelve passing tests built entirely on a two-activity
dictionary, and never once met the output of the engine it was supposed to serve - which is why
it crashed on the first real call.

The reference file is not committed (samples/ and *.xer are git-ignored by CLAUDE.md, and the
only public fixture found is GPL-3.0). Tests that need it skip with an explanatory message and
`docs/P6_EXPORT.md` says how to fetch it.
"""

from __future__ import annotations

import io
import pathlib
import re
from datetime import datetime

import pytest

from backend.app.p6 import COLUMNS, HOURS_PER_DAY, TABLE_ORDER, export_bytes, export_xer

REFERENCE = pathlib.Path(__file__).resolve().parents[2] / 'samples' / 'reference.xer'
ANCHOR = datetime(2026, 4, 6, 0, 0)  # a Monday, so the schedule starts on a working day

#: A brief with enough in it to exercise the export: a Tier IV 2N build produces every activity
#: type, both relationship types, a multi-level WBS and Tier-1 safety work.
BRIEF = {
    'project_name': 'Export Test DC', 'city': 'Chennai', 'tier': 'IV',
    'it_load_mw': 30.0, 'redundancy_topology': '2N', 'site_context': 'brownfield',
}
ANSWER = 'Proceed with the estimates'

pytest_plugins: list = []


# ------------------------------------------------------------------ the real simulation

@pytest.fixture(scope='module')
def real_output():
    """A genuine completed run, walked to the end and answered at every fork.

    Uses the stub provider so the test is free and repeatable, but everything downstream of the
    reasoning - the assembly, the logic, the forward pass that produces the dates - is the real
    engine doing its real work.
    """
    from backend.app.llm_stub import StubAdapter
    from backend.app.simulator import DecisionAnswer, Simulator

    simulator = Simulator(BRIEF, run_id='xer-export', adapter=StubAdapter())
    for _ in range(40):
        list(simulator.run())
        if not simulator.is_halted:
            break
        for fork in sorted(simulator.state.pending_decisions):
            simulator.answer(DecisionAnswer(decision_point_id=fork, answer=ANSWER))
    assert not simulator.is_halted, 'the run never completed, so there is nothing to export'

    output = simulator.output().model_dump()
    assert output['activities'], 'the run assembled no activities'
    assert output['rfs_day'] > 0, 'the run produced no schedule to export'
    return output


@pytest.fixture(scope='module')
def exported(real_output):
    return export_xer(real_output, start_date=ANCHOR)


@pytest.fixture(scope='module')
def parsed(exported, tmp_path_factory):
    """Read our own file back with a third-party parser."""
    xer_reader = pytest.importorskip('xer_reader', reason='xer-reader is needed to read back')
    path = tmp_path_factory.mktemp('xer') / 'export.xer'
    path.write_bytes(exported.encode('cp1252', errors='replace'))
    return xer_reader.XerReader(str(path)).to_dict()


@pytest.fixture(scope='module')
def row_for(real_output, parsed):
    """activity id -> the TASK row it became, matched on NAME rather than on activity code.

    This matters more than it looks. An earlier version of these tests looked rows up with the
    exporter's own `task_codes()`, which meant the test agreed with the exporter about identity
    however wrong the exporter was: mutating the code generator back to the ten-character
    truncation that broke the previous attempt left all sixteen tests green. Names are written
    into the file verbatim, so matching on them is an independent channel and that mutation
    fails as it should.
    """
    names = [a['name'] for a in real_output['activities']]
    assert len(set(names)) == len(names), (
        'this run has duplicate activity names, so names cannot serve as the identity channel'
    )
    by_name = {row['task_name']: row for row in parsed['TASK'].entries()}
    id_to_name = {str(a['id']): a['name'] for a in real_output['activities']}
    return lambda ident: by_name[id_to_name[str(ident)]]


# ------------------------------------------------ 1. structure, against a real P6 export

def _reference_columns():
    columns, table = {}, None
    with io.open(REFERENCE, encoding='cp1252') as handle:
        for line in handle:
            parts = line.rstrip('\n').split('\t')
            if parts[0] == '%T':
                table = parts[1]
            elif parts[0] == '%F':
                columns[table] = tuple(parts[1:])
    return columns


@pytest.mark.skipif(not REFERENCE.is_file(), reason='samples/reference.xer absent — see docs/P6_EXPORT.md')
def test_every_column_list_matches_the_reference_export():
    """Order is not cosmetic: %R rows are positional, so one column out of place shifts every
    value after it and P6 reads a plausible-looking file full of wrong data."""
    reference = _reference_columns()
    for table, ours in COLUMNS.items():
        assert table in reference, f'{table} is not in the reference export'
        assert ours == reference[table], (
            f'{table} columns differ from the reference.\n'
            f'  ours: {ours}\n  ref : {reference[table]}'
        )


@pytest.mark.skipif(not REFERENCE.is_file(), reason='samples/reference.xer absent — see docs/P6_EXPORT.md')
def test_the_reference_itself_parses_with_the_same_reader(real_output):
    """Anchors the round-trip: the parser used to read our file back reads a real P6 file too,
    so a pass is evidence about the format rather than about our own conventions."""
    xer_reader = pytest.importorskip('xer_reader')
    reader = xer_reader.XerReader(str(REFERENCE))
    assert reader.export_version == '5.0'
    assert 'TASK' in reader.get_table_names()


# ---------------------------------------------------------- 2. the file is well formed

def test_the_file_has_the_shape_xer_requires(exported):
    lines = exported.splitlines()
    assert lines[0].startswith('ERMHDR\t5.0\t'), 'missing or wrong ERMHDR'
    assert lines[-1] == '%E', 'file does not end with %E'

    tables = [line.split('\t')[1] for line in lines if line.startswith('%T\t')]
    assert tables == list(TABLE_ORDER), f'tables out of order: {tables}'

    # Every row must carry exactly as many values as its table declares columns.
    current = None
    for line in lines:
        parts = line.split('\t')
        if parts[0] == '%T':
            current = parts[1]
        elif parts[0] == '%R':
            assert len(parts) - 1 == len(COLUMNS[current]), (
                f'{current} row has {len(parts) - 1} values for {len(COLUMNS[current])} columns'
            )


def test_it_is_written_in_the_encoding_p6_exports(real_output):
    assert export_bytes(real_output, start_date=ANCHOR).decode('cp1252').startswith('ERMHDR')


def test_re_exporting_the_same_plan_gives_the_same_bytes(real_output):
    """No clock, no random ids. A diff between two exports should mean the plan changed."""
    first = export_bytes(real_output, start_date=ANCHOR)
    second = export_bytes(real_output, start_date=ANCHOR)
    assert first == second


# --------------------------------------------------------- 3. the round trip, on real output

def test_every_activity_survives_the_round_trip(real_output, parsed):
    tasks = parsed['TASK'].entries()
    assert len(tasks) == len(real_output['activities']), (
        f"{len(real_output['activities'])} activities went in, {len(tasks)} came back"
    )


def test_every_activity_code_traces_back_to_its_activity(real_output, parsed):
    '''Uniqueness alone is not enough: a generator that truncates hard and then
    disambiguates with -2, -3, -4 is unique and useless, because a planner opening the file
    cannot tell which activity `hold.super-2` is. Each code must carry as much of its id as
    P6's forty characters allow.'''
    ids_by_name = {a['name']: str(a['id']) for a in real_output['activities']}
    for row in parsed['TASK'].entries():
        ident = ids_by_name[row['task_name']]
        readable = re.sub(r'[^A-Za-z0-9._-]+', '-', ident).strip('-')
        keep = min(len(readable), 33)
        assert str(row['task_code']).startswith(readable[:keep]), (
            f"code {row['task_code']} is not traceable to activity {ident}"
        )
        assert len(str(row['task_code'])) <= 40, 'P6 codes are limited to 40 characters'


def test_activity_codes_are_unique_after_the_round_trip(real_output, parsed):
    """The failure that sank the previous attempt: ids truncated to ten characters turned
    thirty-five activities into ten codes, and P6 would have merged them."""
    codes = [row['task_code'] for row in parsed['TASK'].entries()]
    assert len(codes) == len(set(codes)), 'duplicate activity codes in the exported file'
    assert len(codes) == len(real_output['activities'])


def test_every_date_matches_the_schedule_it_came_from(real_output, row_for):
    """Dates are the engine's forward pass on the caller's anchor - so they are checkable
    exactly, not approximately."""
    from datetime import timedelta

    anchor = ANCHOR.replace(hour=8)
    for activity in real_output['activities']:
        row = row_for(activity['id'])
        expect_start = anchor + timedelta(days=int(activity['start_day']))
        expect_finish = anchor + timedelta(days=int(activity['finish_day']))
        assert row['early_start_date'] == expect_start, f"{activity['id']} start"
        assert row['early_end_date'] == expect_finish, f"{activity['id']} finish"
        assert row['target_start_date'] == expect_start, f"{activity['id']} target start"
        assert row['target_end_date'] == expect_finish, f"{activity['id']} target finish"


def test_every_duration_matches(real_output, row_for):
    for activity in real_output['activities']:
        row = row_for(activity['id'])
        assert row['target_drtn_hr_cnt'] == int(activity['duration_days']) * HOURS_PER_DAY


def test_every_dependency_survives_with_its_type_and_lag(real_output, parsed):
    """The logic is the plan. An export that loses a link exports a different programme.

    Compared by activity NAME on both sides, so a bug in the activity-code generator cannot
    make the two sides quietly agree with each other.
    """
    name_of = {str(a['id']): a['name'] for a in real_output['activities']}

    expected = set()
    for activity in real_output['activities']:
        for link in activity.get('predecessors') or []:
            if str(link.get('id')) not in name_of:
                continue  # a link outside this plan constrains nothing in it
            expected.add((
                name_of[str(link['id'])], activity['name'],
                str(link.get('type') or 'FS').upper(), int(link.get('lag') or 0),
            ))

    task_by_id = {row['task_id']: row['task_name'] for row in parsed['TASK'].entries()}
    actual = set()
    for row in parsed['TASKPRED'].entries():
        actual.add((
            task_by_id[row['pred_task_id']], task_by_id[row['task_id']],
            str(row['pred_type']).replace('PR_', ''),
            int(row['lag_hr_cnt']) // HOURS_PER_DAY,
        ))

    assert expected, 'the run produced no logic links, so this proves nothing'
    assert actual == expected, (
        f'{len(expected - actual)} links lost, {len(actual - expected)} invented'
    )


def test_milestones_come_back_as_milestones(real_output, row_for):
    milestones = [a for a in real_output['activities'] if a.get('type') in ('milestone', 'gate')]
    assert milestones, 'the run produced no milestones, so this proves nothing'
    for activity in milestones:
        row = row_for(activity['id'])
        assert row['task_type'] == 'TT_Mile', f"{activity['id']} is not a milestone in the file"


def test_the_wbs_is_the_engines_own_breakdown(real_output, parsed):
    """The engine assigns every activity a `stage.package.activity` WBS code. The exported WBS
    must be that structure, not a list of stages invented at export time."""
    nodes = parsed['PROJWBS'].entries()
    roots = [n for n in nodes if n['proj_node_flag']]
    assert len(roots) == 1, 'a P6 project needs exactly one WBS root'

    prefixes = {
        str(a['wbs_id']).split('.')[0] for a in real_output['activities'] if a.get('wbs_id')
    }
    assert len(prefixes) > 1, 'the run produced a flat WBS, so this proves nothing'
    short_names = {str(n['wbs_short_name']) for n in nodes if not n['proj_node_flag']}
    assert short_names == prefixes, f'WBS nodes {short_names} != engine prefixes {prefixes}'

    # Every node except the root hangs off the root, and every task off a node that exists.
    wbs_ids = {n['wbs_id'] for n in nodes}
    for node in nodes:
        if not node['proj_node_flag']:
            assert node['parent_wbs_id'] == roots[0]['wbs_id']
    for row in parsed['TASK'].entries():
        assert row['wbs_id'] in wbs_ids, f"{row['task_code']} points at a missing WBS node"


def test_each_activity_lands_under_its_own_wbs_node(real_output, parsed, row_for):
    node_by_prefix = {
        str(n['wbs_short_name']): n['wbs_id'] for n in parsed['PROJWBS'].entries()
        if not n['proj_node_flag']
    }
    for activity in real_output['activities']:
        prefix = str(activity['wbs_id']).split('.')[0]
        row = row_for(activity['id'])
        assert row['wbs_id'] == node_by_prefix[prefix], (
            f"{activity['id']} (WBS {activity['wbs_id']}) landed under the wrong node"
        )


def test_the_project_end_matches_the_computed_rfs(real_output, parsed):
    from datetime import timedelta

    project = parsed['PROJECT'].entries()[0]
    expected = ANCHOR.replace(hour=8) + timedelta(days=int(real_output['rfs_day']))
    assert project['plan_end_date'] == expected


# ------------------------------------------------------------------ 4. refusals

def test_exporting_a_run_with_no_activities_is_refused():
    """Better an error than a valid-looking file describing nothing."""
    with pytest.raises(ValueError, match='no activities'):
        export_xer({'activities': [], 'project_meta': {}}, start_date=ANCHOR)


def test_a_tab_in_a_name_cannot_break_the_row(real_output):
    """Tabs and newlines are the record separators; a name containing one would split the row."""
    output = dict(real_output)
    output['activities'] = [dict(real_output['activities'][0])]
    output['activities'][0]['name'] = 'Pour\traft\nslab'
    text = export_xer(output, start_date=ANCHOR)
    row = next(l for l in text.splitlines() if l.startswith('%R') and 'Pour' in l)
    assert len(row.split('\t')) - 1 == len(COLUMNS['TASK'])


# ---------------------------------------------------------------- 5. the endpoint

@pytest.fixture()
def completed_run(real_output):
    """The same real run, in the registry, as the endpoint would find it."""
    from backend.app.llm_stub import StubAdapter
    from backend.app.simulator import DecisionAnswer, Simulator, registry

    registry.clear()
    simulator = Simulator(BRIEF, run_id='xer-endpoint', adapter=StubAdapter())
    for _ in range(40):
        list(simulator.run())
        if not simulator.is_halted:
            break
        for fork in sorted(simulator.state.pending_decisions):
            simulator.answer(DecisionAnswer(decision_point_id=fork, answer=ANSWER))
    registry.add(simulator)
    yield simulator
    registry.clear()


@pytest.fixture()
def client():
    from fastapi.testclient import TestClient

    from backend.app.main import app

    return TestClient(app)


def test_the_endpoint_downloads_the_plan_as_a_file(client, completed_run):
    response = client.get(
        '/export/xer-endpoint.xer',
        params={'start_date': '2026-04-06', 'signed_by': 'A. Planner'},
    )
    assert response.status_code == 200, response.text
    assert 'attachment;' in response.headers['content-disposition']
    assert response.headers['content-disposition'].endswith('.xer"')
    body = response.content.decode('cp1252')
    assert body.startswith('ERMHDR\t5.0\t')
    assert body.rstrip().endswith('%E')


def test_the_downloaded_file_is_the_run_it_claims_to_be(client, completed_run):
    """The headers say how many activities and how long; the file must agree, so a reader can
    tell at a glance whether they downloaded the plan they were looking at."""
    response = client.get(
        '/export/xer-endpoint.xer', params={'signed_by': 'A. Planner'}
    )
    assert response.status_code == 200
    output = completed_run.output().model_dump()
    assert int(response.headers['x-plan-activity-count']) == len(output['activities'])
    assert int(response.headers['x-plan-rfs-day']) == output['rfs_day']
    rows = [l for l in response.content.decode('cp1252').splitlines() if l.startswith('%R')]
    task_rows = response.content.decode('cp1252').split('%T\tTASK')[1].split('%T\t')[0]
    assert task_rows.count('\n%R') == len(output['activities'])
    assert rows


def test_tier_1_safety_blocks_the_export_until_someone_signs(client, completed_run):
    """CLAUDE.md rule 5. The run is finished and the plan is sound; what is missing is a human
    accepting the Tier-1 safety activities, and the refusal says which ones."""
    quality = completed_run.output().model_dump()['quality']
    assert quality['export_blocked'], 'this run has no Tier-1 activities, so this proves nothing'

    response = client.get('/export/xer-endpoint.xer')
    assert response.status_code == 409
    detail = response.json()['detail']
    assert detail['reason'] == 'tier_1_signoff_required'
    assert detail['tier_1_ids'], 'the refusal did not say which activities need signing'
    assert set(detail['tier_1_ids']) == set(quality['tier_1_ids'])


def test_the_signature_is_written_into_the_file(client, completed_run):
    """The export records who released it, rather than the sign-off living only in a log."""
    response = client.get(
        '/export/xer-endpoint.xer', params={'signed_by': 'R. Mehta (HSE)'}
    )
    assert response.status_code == 200
    assert 'R. Mehta (HSE)' in response.content.decode('cp1252')


def test_an_unfinished_run_is_not_exportable(client):
    """A partial plan renders fine and exports as a programme with stages silently missing."""
    from backend.app.llm_stub import StubAdapter
    from backend.app.simulator import Simulator, registry

    registry.clear()
    halted = Simulator(BRIEF, run_id='xer-halted', adapter=StubAdapter())
    list(halted.run())
    assert halted.is_halted, 'the run did not halt, so this proves nothing'
    registry.add(halted)
    try:
        response = client.get('/export/xer-halted.xer', params={'signed_by': 'A. Planner'})
        assert response.status_code == 409
        assert 'has not finished' in response.json()['detail']
    finally:
        registry.clear()


def test_an_unknown_run_is_a_404(client):
    assert client.get('/export/run-nope.xer').status_code == 404


def test_a_malformed_start_date_is_refused_rather_than_guessed(client, completed_run):
    response = client.get(
        '/export/xer-endpoint.xer',
        params={'start_date': '06-04-2026', 'signed_by': 'A. Planner'},
    )
    assert response.status_code == 400
    assert 'YYYY-MM-DD' in response.json()['detail']
