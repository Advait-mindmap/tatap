"""Write a completed SimulationOutput as a Primavera P6 XER file.

XER is a tab-delimited text format, not binary and not XML. Its shape is:

    ERMHDR<TAB>version<TAB>date<TAB>...          one header line
    %T<TAB>TABLENAME                             a table begins
    %F<TAB>col<TAB>col<TAB>...                   its columns, in order
    %R<TAB>val<TAB>val<TAB>...                   one row, values positional
    %E                                           end of file

Every column list in this module is transcribed from `samples/reference.xer`, a real P6 5.0
export (see docs/P6_EXPORT.md). `test_xer_export.py` asserts each list still matches that file
column for column, so the structure is checked against a real export rather than against my
memory of the spec.

WHAT THIS WRITES, and what it does not:

* Only what the completed run actually contains. Every activity, duration, logic link and WBS
  node comes from the SimulationOutput. Nothing is padded out to look like a fuller schedule.
* Dates are the engine's forward pass (engine/schedule.py) mapped onto a project start date the
  caller supplies. The engine computes day OFFSETS; it does not know when the project begins, so
  the anchor is an input rather than a guess. Given the same anchor the dates are exact, not
  approximate: activity start = anchor + start_day, finish = anchor + finish_day.
* Costs, resources and actuals are absent because the simulation has none. An empty TASKRSRC is
  honest; a fabricated one would not be.
"""

from __future__ import annotations

import hashlib
import re
from datetime import datetime, timedelta
from typing import Any, Dict, Iterable, List, Sequence, Tuple

#: The export version the column lists below were transcribed from.
XER_VERSION = '5.0'

#: P6 works in hours. The engine works in whole days; this is the bridge, and it is also the
#: `day_hr_cnt` written into the calendar so the two agree inside P6.
HOURS_PER_DAY = 8

#: Column orders, transcribed verbatim from samples/reference.xer. Order is not cosmetic: %R
#: rows are positional, so a column out of place silently shifts every value after it.
COLUMNS: Dict[str, Tuple[str, ...]] = {
    'CURRTYPE': (
        'curr_id', 'decimal_digit_cnt', 'curr_symbol', 'decimal_symbol', 'digit_group_symbol',
        'pos_curr_fmt_type', 'neg_curr_fmt_type', 'curr_type', 'curr_short_name',
        'group_digit_cnt', 'base_exch_rate',
    ),
    'OBS': ('obs_id', 'parent_obs_id', 'guid', 'seq_num', 'obs_name', 'obs_descr'),
    'PROJECT': (
        'proj_id', 'fy_start_month_num', 'chng_eff_cmp_pct_flag', 'rsrc_self_add_flag',
        'allow_complete_flag', 'rsrc_multi_assign_flag', 'ts_rsrc_mark_act_finish_flag',
        'ts_rsrc_vw_inact_actv_flag', 'checkout_flag', 'project_flag', 'step_complete_flag',
        'cost_qty_recalc_flag', 'sum_only_flag', 'batch_sum_flag', 'name_sep_char',
        'def_complete_pct_type', 'proj_short_name', 'acct_id', 'orig_proj_id', 'source_proj_id',
        'base_type_id', 'clndr_id', 'sum_base_proj_id', 'task_code_base', 'task_code_step',
        'priority_num', 'wbs_max_sum_level', 'risk_level', 'strgy_priority_num', 'last_checksum',
        'critical_drtn_hr_cnt', 'def_cost_per_qty', 'last_recalc_date', 'plan_start_date',
        'plan_end_date', 'scd_end_date', 'add_date', 'sum_data_date', 'last_tasksum_date',
        'fcst_start_date', 'def_duration_type', 'task_code_prefix', 'guid', 'def_qty_type',
        'add_by_name', 'web_local_root_path', 'proj_url', 'def_rate_type', 'add_act_remain_flag',
        'act_this_per_link_flag', 'def_task_type', 'act_pct_link_flag', 'critical_path_type',
        'task_code_prefix_flag', 'def_rollup_dates_flag', 'use_project_baseline_flag',
        'rem_target_link_flag', 'reset_planned_flag', 'allow_neg_act_flag', 'sum_assign_level',
        'last_fin_dates_id', 'last_baseline_update_date', 'cr_external_key', 'apply_actuals_date',
        'intg_proj_type', 'loaded_scope_level', 'export_flag', 'new_fin_dates_id',
        'next_data_date', 'close_period_flag', 'trsrcsum_loaded',
    ),
    'CALENDAR': (
        'clndr_id', 'default_flag', 'clndr_name', 'proj_id', 'base_clndr_id', 'last_chng_date',
        'clndr_type', 'day_hr_cnt', 'week_hr_cnt', 'month_hr_cnt', 'year_hr_cnt', 'clndr_data',
    ),
    'PROJWBS': (
        'wbs_id', 'proj_id', 'obs_id', 'seq_num', 'est_wt', 'proj_node_flag', 'sum_data_flag',
        'status_code', 'wbs_short_name', 'wbs_name', 'phase_id', 'parent_wbs_id', 'ev_user_pct',
        'ev_etc_user_value', 'orig_cost', 'indep_remain_total_cost', 'ann_dscnt_rate_pct',
        'dscnt_period_type', 'indep_remain_work_qty', 'anticip_start_date', 'anticip_end_date',
        'ev_compute_type', 'ev_etc_compute_type', 'guid', 'tmpl_guid', 'plan_open_state',
    ),
    'TASK': (
        'task_id', 'proj_id', 'wbs_id', 'clndr_id', 'est_wt', 'phys_complete_pct',
        'rev_fdbk_flag', 'lock_plan_flag', 'auto_compute_act_flag', 'complete_pct_type',
        'task_type', 'duration_type', 'review_type', 'status_code', 'task_code', 'task_name',
        'rsrc_id', 'total_float_hr_cnt', 'free_float_hr_cnt', 'remain_drtn_hr_cnt',
        'act_work_qty', 'remain_work_qty', 'target_work_qty', 'target_drtn_hr_cnt',
        'target_equip_qty', 'act_equip_qty', 'remain_equip_qty', 'cstr_date', 'act_start_date',
        'act_end_date', 'late_start_date', 'late_end_date', 'expect_end_date',
        'early_start_date', 'early_end_date', 'restart_date', 'reend_date', 'target_start_date',
        'target_end_date', 'review_end_date', 'rem_late_start_date', 'rem_late_end_date',
        'cstr_type', 'priority_type', 'suspend_date', 'resume_date', 'float_path',
        'float_path_order', 'guid', 'tmpl_guid', 'cstr_date2', 'cstr_type2',
        'driving_path_flag', 'act_this_per_work_qty', 'act_this_per_equip_qty',
        'external_early_start_date', 'external_late_end_date', 'create_date', 'update_date',
        'create_user', 'update_user',
    ),
    'TASKPRED': (
        'task_pred_id', 'task_id', 'pred_task_id', 'proj_id', 'pred_proj_id', 'pred_type',
        'lag_hr_cnt', 'float_path', 'aref', 'arls',
    ),
}

#: The order tables appear in the file. P6 reads a table's foreign keys as it goes, so a table
#: must not precede the one it points at.
TABLE_ORDER = ('CURRTYPE', 'OBS', 'PROJECT', 'CALENDAR', 'PROJWBS', 'TASK', 'TASKPRED')

#: Our activity types, mapped to P6's. A milestone has no duration, which is what P6 means by
#: TT_Mile; a gate or hold point is a milestone in the same sense.
TASK_TYPE = {
    'task': 'TT_Task',
    'milestone': 'TT_Mile',
    'gate': 'TT_Mile',
    'hold_point': 'TT_Mile',
}

#: Relationship types. Anything unrecognised becomes finish-to-start, which is the conservative
#: reading and matches what the scheduler does with the same input.
PRED_TYPE = {'FS': 'PR_FS', 'SS': 'PR_SS', 'FF': 'PR_FF', 'SF': 'PR_SF'}

#: A single 8-hour, 5-day calendar. P6 stores calendars as its own nested format; this is the
#: minimum well-formed value, with all five weekdays worked 08:00-16:00.
_CALENDAR_DATA = (
    '(0||CalendarData()('
    '   (0||DaysOfWeek()('
    '      (0||1()())'
    '      (0||2()(   (0||0(s|08:00|f|16:00)())))'
    '      (0||3()(   (0||0(s|08:00|f|16:00)())))'
    '      (0||4()(   (0||0(s|08:00|f|16:00)())))'
    '      (0||5()(   (0||0(s|08:00|f|16:00)())))'
    '      (0||6()(   (0||0(s|08:00|f|16:00)())))'
    '      (0||7()())))'
    '   (0||Exceptions()())))'
)

_SAFE_CODE = re.compile(r'[^A-Za-z0-9._-]+')


def _stamp(when: datetime) -> str:
    """P6's date format. Minute precision, no seconds, no timezone."""
    return when.strftime('%Y-%m-%d %H:%M')


def _clean(value: Any) -> str:
    """One field value.

    Tabs and newlines are the record separators, so a value containing either would silently
    split the row into pieces. They become spaces.
    """
    if value is None:
        return ''
    return re.sub(r'[\t\r\n]+', ' ', str(value)).strip()


def _row(table: str, values: Dict[str, Any]) -> str:
    """A %R line, values placed in the table's declared column order."""
    return '\t'.join(['%R'] + [_clean(values.get(col, '')) for col in COLUMNS[table]])


def task_codes(activities: Sequence[Dict[str, Any]]) -> Dict[str, str]:
    """activity id -> a unique P6 activity code.

    P6 allows 40 characters and requires uniqueness within a project. This engine's ids run to
    81 (`hold.superstructure.frag.superstructure.steel.b20.weld-and-bolt-torque-inspection`), so
    truncation is unavoidable - and truncation is exactly where uniqueness breaks. Ids that share
    a fragnet share a long prefix, so cutting to 40 characters collapses whole fragnets into one
    code, and P6 would treat those as the same activity.

    So: use the id where it fits, and otherwise keep 33 characters of it and append a hash of
    the WHOLE id. The result is readable, and it is stable - it depends only on the id, not on
    the order activities happen to arrive in. The loop afterwards is a belt-and-braces check
    against a hash collision, not the main defence.
    """
    codes: Dict[str, str] = {}
    used: set = set()
    for activity in activities:
        ident = str(activity.get('id') or '')
        raw = _SAFE_CODE.sub('-', ident).strip('-') or 'ACT'
        if len(raw) <= 40:
            code = raw
        else:
            digest = hashlib.sha256(ident.encode('utf-8')).hexdigest()[:6]
            code = f'{raw[:33]}-{digest}'
        if code in used:
            for n in range(2, 10_000):
                suffix = f'-{n}'
                candidate = f'{code[:40 - len(suffix)]}{suffix}'
                if candidate not in used:
                    code = candidate
                    break
        used.add(code)
        codes[ident] = code
    return codes


def _zone_label(zone_id: str) -> str:
    """`zone.electrical-room.01` -> `Electrical room 01`."""
    parts = str(zone_id or '').split('.')
    if len(parts) >= 3:
        return f"{parts[1].replace('-', ' ').replace('_', ' ').capitalize()} {parts[2]}"
    return str(zone_id or 'Unzoned')


def build_wbs(
    output: Dict[str, Any], project_name: str, proj_id: int, obs_id: int,
) -> Tuple[List[Dict[str, Any]], Dict[str, int]]:
    """The run's own breakdown structure, as a TREE rather than a single flat level.

    The engine assigns every activity a three-part code - `stage.package.activity`, e.g.
    `05.01.003` - plus a `00.*` bucket for cross-stage gates, long-lead deliveries and statutory
    approvals. Two of those three levels used to be discarded: the export grouped on
    `code.split('.')[0]` alone, so a thirteen-stage plan became fourteen WBS nodes and every
    activity hung directly off its stage. In P6 that is a schedule nobody can navigate.

    Levels are now stage -> package -> zone, and a level is created ONLY WHERE IT
    DISCRIMINATES. A node with a single child is not structure, it is a step the reader has to
    click through; and P6 is happy to carry tasks at any level, so a stage with one package
    keeps its activities directly beneath it.

    WHAT THIS DOES NOT FIX, measured rather than assumed. Nesting adds far less than it looks
    like it should, because the data it nests is thin in two known places: every stage carries
    exactly ONE package (the fragnet library has one per stage), and every zone reference ends
    in `.01` (the engine collapses all seven data halls onto the first). So the package level
    discriminates in one bucket and the zone level in one bucket, and the tree goes from 14
    nodes to about 20 rather than to the 40-60 a three-level code implies. The structure is now
    correct and ready; the depth arrives when there are packages and zones to break out.

    Returns the nodes and a map from ACTIVITY ID to the wbs_id it belongs under.
    """
    activities = list(output.get('activities') or [])

    def stage_of(activity: Dict[str, Any]) -> str:
        return str(activity.get('wbs_id') or '').split('.')[0] or '99'

    def package_of(activity: Dict[str, Any]) -> str:
        parts = str(activity.get('wbs_id') or '').split('.')
        return parts[1] if len(parts) > 1 else ''

    # ---- stage order and naming, taken from the plan rather than a fixed list
    stage_meta: Dict[str, Dict[str, Any]] = {}
    for activity in activities:
        code = stage_of(activity)
        day = int(activity.get('start_day') or 0)
        meta = stage_meta.setdefault(code, {'day': day, 'names': []})
        meta['day'] = min(meta['day'], day)
        name = str(activity.get('stage') or '')
        if name and name not in meta['names']:
            meta['names'].append(name)

    nodes: List[Dict[str, Any]] = []
    assignment: Dict[str, int] = {}
    next_id = proj_id * 1000
    root_id = next_id

    def add(name: str, short: str, parent: Any, seq: int) -> int:
        nonlocal next_id
        next_id += 1
        nodes.append({
            'wbs_id': next_id, 'proj_id': proj_id, 'obs_id': obs_id, 'seq_num': seq,
            'est_wt': 1, 'proj_node_flag': 'N', 'sum_data_flag': 'N', 'status_code': 'WS_Open',
            'wbs_short_name': short[:20], 'wbs_name': name[:100], 'parent_wbs_id': parent,
            'ev_user_pct': 0, 'ev_etc_user_value': 0,
            'ev_compute_type': 'EC_Cmp_pct', 'ev_etc_compute_type': 'EE_Rem_hr',
        })
        return next_id

    nodes.append({
        'wbs_id': root_id, 'proj_id': proj_id, 'obs_id': obs_id, 'seq_num': 0, 'est_wt': 1,
        'proj_node_flag': 'Y', 'sum_data_flag': 'N', 'status_code': 'WS_Open',
        'wbs_short_name': (project_name or 'PROJECT')[:20],
        'wbs_name': project_name or 'Project', 'parent_wbs_id': '',
        'ev_user_pct': 0, 'ev_etc_user_value': 0,
        'ev_compute_type': 'EC_Cmp_pct', 'ev_etc_compute_type': 'EE_Rem_hr',
    })

    # Ordered by the stage CODE, which is the canonical order of the works, not by date.
    # Sorting by start day put fire_bms (day 95) above enabling (day 210, held behind the
    # environmental clearance): true about the dates, wrong for a breakdown structure, which a
    # planner reads as the order of the works. `00` sorts first on its own, which is where the
    # cross-stage gates, deliveries and approvals belong.
    ordered_stages = sorted(stage_meta)
    for stage_index, stage_code in enumerate(ordered_stages, start=1):
        in_stage = [a for a in activities if stage_of(a) == stage_code]
        names = stage_meta[stage_code]['names']
        if stage_code == '00':
            label = 'Cross-stage gates, deliveries and approvals'
        else:
            label = ' / '.join(n.replace('_', ' ').title() for n in names) or stage_code
        stage_node = add(label, stage_code, root_id, stage_index * 10)

        # ---- package level, only where more than one package exists in this stage
        packages = sorted({package_of(a) for a in in_stage if package_of(a)})
        groups: List[Tuple[int, List[Dict[str, Any]]]] = []
        if len(packages) > 1:
            for package_index, package in enumerate(packages, start=1):
                members = [a for a in in_stage if package_of(a) == package]
                package_node = add(
                    _package_label(package, members), f'{stage_code}.{package}',
                    stage_node, package_index * 10,
                )
                groups.append((package_node, members))
        else:
            groups.append((stage_node, in_stage))

        # ---- zone level, only where more than one zone appears under a parent
        for parent_node, members in groups:
            zones = sorted({str(a.get('zone_id')) for a in members if a.get('zone_id')})
            if len(zones) > 1:
                zone_nodes = {}
                for zone_index, zone in enumerate(zones, start=1):
                    zone_nodes[zone] = add(
                        _zone_label(zone), zone.split('.')[-2][:20] if '.' in zone else zone,
                        parent_node, zone_index * 10,
                    )
                for activity in members:
                    zone = str(activity.get('zone_id')) if activity.get('zone_id') else ''
                    assignment[str(activity.get('id'))] = zone_nodes.get(zone, parent_node)
            else:
                for activity in members:
                    assignment[str(activity.get('id'))] = parent_node

    return nodes, assignment


#: The `00.*` bucket codes the KIND of constraint in two letters, the engine building the code as
#: `00.{kind[:2]}.{n}`. Deriving the name from the members instead collapsed three different kinds
#: into three sibling nodes all reading "Cross-stage gates", which is worse than a bare code.
PACKAGE_NAMES = {
    'de': 'Design release gates',
    'dl': 'Long-lead deliveries',
    'pr': 'Predecessor-stage gates',
    're': 'Readiness gates',
    'st': 'Statutory approvals',
}


def _package_label(package: str, members: Sequence[Dict[str, Any]]) -> str:
    """Name a package. The two-letter code alone says nothing to a planner reading it in P6."""
    if package in PACKAGE_NAMES:
        return PACKAGE_NAMES[package]
    fragnets = sorted({str(a.get('source_fragnet')) for a in members if a.get('source_fragnet')})
    if len(fragnets) == 1:
        return fragnets[0].replace('frag.', '').replace('.', ' ').replace('_', ' ').title()
    return f'Package {package}'


def export_xer(
    output: Dict[str, Any],
    *,
    start_date: datetime,
    project_short_name: str = 'DCPLAN',
    exported_by: str = 'dc-planner',
) -> str:
    """Render a completed SimulationOutput as XER text.

    `start_date` anchors the schedule. The engine produces day offsets from day 0; only the
    caller knows when day 0 is, so it is required rather than defaulted to today behind the
    caller's back.
    """
    activities = list(output.get('activities') or [])
    if not activities:
        raise ValueError('nothing to export: this run assembled no activities')

    meta = output.get('project_meta') or {}
    project_name = str(meta.get('project_name') or 'Data centre project')

    proj_id, clndr_id, obs_id, curr_id = 1, 1, 1, 1
    anchor = start_date.replace(hour=8, minute=0, second=0, microsecond=0)
    rfs_day = int(output.get('rfs_day') or 0)

    def at(day: int) -> datetime:
        return anchor + timedelta(days=int(day or 0))

    codes = task_codes(activities)
    wbs_nodes, wbs_of_activity = build_wbs(output, project_name, proj_id, obs_id)
    root_wbs = wbs_nodes[0]['wbs_id']

    task_ids: Dict[str, int] = {
        str(a.get('id')): 1000 + i for i, a in enumerate(activities)
    }

    lines: List[str] = []
    now = _stamp(datetime(2000, 1, 1, 0, 0))  # fixed: a re-export of the same plan is identical

    lines.append('\t'.join([
        'ERMHDR', XER_VERSION, anchor.strftime('%Y-%m-%d'), 'Project', exported_by,
        exported_by, 'dbxDatabaseNoName', 'Project Management', 'USD',
    ]))

    def table(name: str, rows: Iterable[Dict[str, Any]]) -> None:
        lines.append(f'%T\t{name}')
        lines.append('\t'.join(['%F'] + list(COLUMNS[name])))
        for values in rows:
            lines.append(_row(name, values))

    table('CURRTYPE', [{
        'curr_id': curr_id, 'decimal_digit_cnt': 2, 'curr_symbol': '$', 'decimal_symbol': '.',
        'digit_group_symbol': ',', 'pos_curr_fmt_type': '#1.1', 'neg_curr_fmt_type': '(#1.1)',
        'curr_type': 'Dollar', 'curr_short_name': 'USD', 'group_digit_cnt': 3,
        'base_exch_rate': 1,
    }])

    table('OBS', [{
        'obs_id': obs_id, 'parent_obs_id': '', 'guid': '', 'seq_num': 0,
        'obs_name': 'Enterprise', 'obs_descr': '',
    }])

    table('PROJECT', [{
        'proj_id': proj_id, 'fy_start_month_num': 1, 'chng_eff_cmp_pct_flag': 'N',
        'rsrc_self_add_flag': 'Y', 'allow_complete_flag': 'Y', 'rsrc_multi_assign_flag': 'Y',
        'ts_rsrc_mark_act_finish_flag': 'N', 'ts_rsrc_vw_inact_actv_flag': 'N',
        'checkout_flag': 'N', 'project_flag': 'Y', 'step_complete_flag': 'N',
        'cost_qty_recalc_flag': 'N', 'sum_only_flag': 'N', 'batch_sum_flag': 'Y',
        'name_sep_char': '.', 'def_complete_pct_type': 'CP_Drtn',
        'proj_short_name': project_short_name[:20], 'clndr_id': clndr_id,
        'task_code_base': 1000, 'task_code_step': 10, 'priority_num': 10,
        'wbs_max_sum_level': 2, 'risk_level': 3, 'strgy_priority_num': 500,
        'critical_drtn_hr_cnt': 0, 'def_cost_per_qty': '0.00',
        'last_recalc_date': _stamp(anchor), 'plan_start_date': _stamp(anchor),
        'plan_end_date': _stamp(at(rfs_day)), 'scd_end_date': _stamp(at(rfs_day)),
        'add_date': now, 'def_duration_type': 'DT_FixedDrtn', 'task_code_prefix': 'A',
        'def_qty_type': 'QT_Hour', 'add_by_name': exported_by,
        'def_rate_type': 'COST_PER_QTY', 'add_act_remain_flag': 'N',
        'act_this_per_link_flag': 'Y', 'def_task_type': 'TT_Task', 'act_pct_link_flag': 'Y',
        'critical_path_type': 'CT_TotFloat', 'task_code_prefix_flag': 'Y',
        'def_rollup_dates_flag': 'Y', 'use_project_baseline_flag': 'Y',
        'rem_target_link_flag': 'Y', 'reset_planned_flag': 'N', 'allow_neg_act_flag': 'N',
        'sum_assign_level': 'SL_Taskrsrc', 'loaded_scope_level': 7, 'export_flag': 'Y',
    }])

    table('CALENDAR', [{
        'clndr_id': clndr_id, 'default_flag': 'Y', 'clndr_name': 'DC Planner 5-day',
        'proj_id': '', 'base_clndr_id': '', 'last_chng_date': now, 'clndr_type': 'CA_Base',
        'day_hr_cnt': HOURS_PER_DAY, 'week_hr_cnt': HOURS_PER_DAY * 5,
        'month_hr_cnt': HOURS_PER_DAY * 5 * 4.33, 'year_hr_cnt': HOURS_PER_DAY * 5 * 52,
        'clndr_data': _CALENDAR_DATA,
    }])

    table('PROJWBS', wbs_nodes)

    task_rows: List[Dict[str, Any]] = []
    for activity in activities:
        ident = str(activity.get('id'))
        start = at(activity.get('start_day'))
        finish = at(activity.get('finish_day'))
        duration_hr = int(activity.get('duration_days') or 0) * HOURS_PER_DAY
        kind = TASK_TYPE.get(str(activity.get('type') or 'task'), 'TT_Task')
        task_rows.append({
            'task_id': task_ids[ident], 'proj_id': proj_id,
            'wbs_id': wbs_of_activity.get(ident, root_wbs),
            'clndr_id': clndr_id, 'est_wt': 1, 'phys_complete_pct': 0,
            'rev_fdbk_flag': 'N', 'lock_plan_flag': 'N', 'auto_compute_act_flag': 'Y',
            'complete_pct_type': 'CP_Drtn', 'task_type': kind,
            'duration_type': 'DT_FixedDrtn', 'review_type': 'RV_OK',
            'status_code': 'TK_NotStart',
            'task_code': codes[ident], 'task_name': activity.get('name') or ident,
            'total_float_hr_cnt': '', 'free_float_hr_cnt': '',
            'remain_drtn_hr_cnt': duration_hr, 'act_work_qty': 0, 'remain_work_qty': 0,
            'target_work_qty': 0, 'target_drtn_hr_cnt': duration_hr,
            'target_equip_qty': 0, 'act_equip_qty': 0, 'remain_equip_qty': 0,
            'early_start_date': _stamp(start), 'early_end_date': _stamp(finish),
            'target_start_date': _stamp(start), 'target_end_date': _stamp(finish),
            'restart_date': _stamp(start), 'reend_date': _stamp(finish),
            'priority_type': 'PT_Normal', 'driving_path_flag': 'N',
            'act_this_per_work_qty': 0, 'act_this_per_equip_qty': 0,
            'create_date': now, 'update_date': now,
            'create_user': exported_by, 'update_user': exported_by,
        })
    table('TASK', task_rows)

    pred_rows: List[Dict[str, Any]] = []
    pred_id = 1
    for activity in activities:
        ident = str(activity.get('id'))
        for link in activity.get('predecessors') or []:
            pred = str(link.get('id') or '')
            if pred not in task_ids:
                continue  # a link to something outside this plan constrains nothing in it
            pred_rows.append({
                'task_pred_id': pred_id, 'task_id': task_ids[ident],
                'pred_task_id': task_ids[pred], 'proj_id': proj_id, 'pred_proj_id': proj_id,
                'pred_type': PRED_TYPE.get(str(link.get('type') or 'FS').upper(), 'PR_FS'),
                'lag_hr_cnt': int(link.get('lag') or 0) * HOURS_PER_DAY,
                'float_path': '', 'aref': '', 'arls': '',
            })
            pred_id += 1
    table('TASKPRED', pred_rows)

    lines.append('%E')
    return '\n'.join(lines) + '\n'


def export_bytes(output: Dict[str, Any], **kwargs: Any) -> bytes:
    """XER as bytes, in the encoding P6 writes.

    P6 exports cp1252, not UTF-8. A name with a character outside that set is transliterated
    rather than failing the export - losing an accent is better than losing the file.
    """
    return export_xer(output, **kwargs).encode('cp1252', errors='replace')


def export_filename(output: Dict[str, Any]) -> str:
    """A filename that says which run this is.

    The run id is always included. Two exports of the same project - a different anchor date, a
    different set of answers - are different programmes, and a downloads folder holding
    `chennai-dc.xer` twice cannot tell you which is which. Intake also frequently leaves
    `project_name` unset, in which case the id is all there is to go on.
    """
    meta = output.get('project_meta') or {}
    run_id = _SAFE_CODE.sub('-', str(meta.get('run_id') or '')).strip('-')
    name = _SAFE_CODE.sub('-', str(meta.get('project_name') or '')).strip('-')
    stem = '-'.join(part for part in (name[:48], run_id) if part) or 'plan'
    return f'{stem}.xer'
