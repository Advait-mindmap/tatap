"""Cross-stage gate machinery.

A fragnet describes work *within* a stage. The dependencies that actually shape a data centre
programme run *between* stages, and DOMAIN_KNOWLEDGE.md §4 names two of them:

- **Engineering drives procurement.** You cannot order to a specification that is not fixed, so
  design completion (IFC drawings issued) gates procurement.
- **"Front-load it; tie construction to delivery."** Long-lead plant usually drives RFS, so each
  long-lead item's delivery is a milestone that gates the construction activity consuming it.

Both are expressed here as DATA, not as code paths:

- `CROSS_STAGE_GATES` is a table of `GateRule`s — producer stage, consumer stages, why.
- Delivery gates are derived from the `material_links` a fragnet already declares.

That matters for a specific reason. `frag.design.*` and `frag.procurement.*` do not exist yet, so
the design stage instances no activities today. A milestone is emitted anyway, *unanchored*, and
still gates its consumers — so the programme carries the constraint now, and the moment those
fragnets are written the milestone simply acquires a predecessor. Nothing here needs rewriting
when the library fills out; that is the whole point of putting the rule in a table.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Sequence, Tuple

from backend.app.engine.ids import gate_id, slug


@dataclass(frozen=True)
class GateRule:
    """One cross-stage constraint: a milestone produced by one stage that gates others."""

    id: str
    label: str
    #: The stage whose completion the milestone represents. If that stage instanced activities,
    #: the milestone hangs off its last one; if not, the milestone stands alone.
    producer_stage: str
    #: Stages whose activities must not start before the milestone.
    consumer_stages: Tuple[str, ...]
    kind: str
    why: str
    #: When true, only the consumer activities that declare a matching material_link are gated,
    #: rather than every activity in the consumer stages.
    per_material_link: bool = False
    #: PARTIAL RELEASE. (fragnet_id, activity_id) pairs the milestone hangs off, instead of every
    #: activity in the producer stage.
    #:
    #: Whole-stage finish-to-start is the conservative reading and it was the right place to
    #: start, but it charges the programme for work the consumer does not actually wait for. Two
    #: real examples, both of which this exists to fix: envelope was waiting for fireproofing to
    #: steel, which is interior work that overlaps cladding; and fit-out was waiting for
    #: envelope water-tightness CLOSE-OUT, when what fit-out actually needs is a hall that is
    #: roofed and clad.
    #:
    #: Naming the milestone rather than inventing a lag is deliberate. "Fit-out follows
    #: weather-tightness" is a construction fact that survives a change in durations; "fit-out
    #: starts sixty days into envelope" is a number that silently goes wrong the moment a
    #: duration changes. If the named activities are not in the plan - a different fragnet was
    #: selected for that stage - the gate falls back to the whole stage and says so.
    producer_activities: Tuple[Tuple[str, str], ...] = ()
    #: Zone kind to stage the release across, e.g. 'data_hall'.
    #:
    #: Set together with `producer_activities`, this turns the release from finish-to-start into
    #: start-to-start with a lead of (producer duration / number of those zones): the consumer
    #: may begin once the FIRST zone is done rather than the last. Cladding forty days across
    #: seven data halls releases fit-out after about six, which is what hall-by-hall working
    #: actually means.
    #:
    #: The divisor is read from the plan's own zones - which the engine already derives from IT
    #: load, tier and topology - rather than being a constant. A seven-hall campus and a
    #: single-hall build get different answers for the same reason a planner would give them
    #: different answers.
    #:
    #: HONESTLY AN APPROXIMATION, in both directions: it lets ALL the consumer work start at the
    #: first zone, where a true per-zone model would start each hall's fit-out at its own hall's
    #: cladding. Waiting for the last hall (what this replaces) was wrong the other way, and by
    #: more.
    release_per_zone_kind: str = ''


#: The declarative gate table. Add a row here, not a code path.
CROSS_STAGE_GATES: Tuple[GateRule, ...] = (
    GateRule(
        id='ifc_issued',
        label='Design complete - IFC drawings issued',
        producer_stage='design',
        consumer_stages=('procurement',),
        kind='design_release',
        why=(
            'Engineering drives procurement (DOMAIN_KNOWLEDGE.md §4). The equipment schedule is '
            'a design output, so ordering before the design is fixed risks buying to a '
            'specification that then changes.'
        ),
    ),
    GateRule(
        id='ifc_construction',
        label='Design complete - IFC drawings issued (construction release)',
        producer_stage='design',
        consumer_stages=(
            'enabling', 'substructure', 'superstructure', 'envelope',
            # MEP too: rough-in is installed to issued drawings like anything else. Delivery
            # gates then hold back only the activities that consume long-lead plant, which is
            # the distinction DOMAIN_KNOWLEDGE.md §4 draws - containment can proceed while the
            # transformer is still being built.
            'mep_power', 'mep_cooling', 'fire_bms', 'fit_out',
        ),
        kind='design_release',
        why=(
            'INTRODUCED FOR REVIEW, not transcribed from a doc. You cannot build to drawings '
            'that have not been issued, so IFC release gates physical construction the same '
            'way it gates procurement. Without this rule every construction stage started on '
            'day one, in parallel with the design that defines it. Real programmes often '
            'release foundations for construction ahead of full IFC, so this is arguably too '
            'strict - a partial-release gate would model it better.'
        ),
    ),
    # ---- the physical build sequence -------------------------------------------------
    #
    # Added after an audit found every construction stage starting on the same day: with only
    # ifc_construction in place, design release freed substructure, superstructure, envelope and
    # fit-out all at once, so the programme had steel erection beginning before the foundations
    # were poured and raised floor going in before the building was weather-tight. Any planner
    # would catch that on sight.
    #
    # These four rules are whole-stage finish-to-start, which is the CONSERVATIVE reading. A real
    # programme overlaps them by zone - steel starts on the foundations that are cured while the
    # rest are still being poured - so the dates these produce are later than a well-run job
    # would achieve. That is the right direction to be wrong in for a bid, but it is still wrong:
    # modelling the overlap properly needs per-zone sub-networks, which do not exist yet. Until
    # they do, the sequence is at least real.
    GateRule(
        id='enabling_complete',
        label='Site established - ready for permanent works',
        producer_stage='enabling',
        consumer_stages=('substructure',),
        kind='predecessor_stage',
        why=(
            'INTRODUCED FOR REVIEW. Foundations are not poured on an unestablished site: access '
            'roads, hoarding, temporary power and the BOCW/CLRA registrations that let labour on '
            'site all precede permanent works. Conservative in the usual way - a real job starts '
            'piling while the site offices are still going up - and it holds substructure behind '
            'the whole enabling package rather than behind the parts that actually matter to it.'
        ),
    ),
    GateRule(
        id='commissioning_complete',
        label='Commissioning complete - facility ready for handover',
        producer_stage='commissioning',
        consumer_stages=('handover',),
        kind='readiness',
        why=(
            'INTRODUCED FOR REVIEW. Handover documents and certifies a facility that works, so '
            'it follows the L1-L5 ladder rather than running beside it. Without this rule the '
            'handover package started on day one, in parallel with design: as-built drawings '
            'before there was anything built, and an Uptime Tier demonstration of a facility '
            'that did not yet exist. This is the same fault the construction-sequence rules fix, '
            'and adding handover work without it would have reintroduced it.'
        ),
    ),
    GateRule(
        id='substructure_complete',
        label='Substructure complete - foundations available for erection',
        producer_stage='substructure',
        consumer_stages=('superstructure',),
        kind='predecessor_stage',
        why=(
            'INTRODUCED FOR REVIEW. You cannot erect a frame on foundations that do not exist. '
            'Without this rule superstructure started on the same day as substructure. Whole-'
            'stage FS is conservative: erection normally begins on the first cured pour rather '
            'than the last, which a per-zone model would capture and this does not.'
        ),
    ),
    GateRule(
        id='superstructure_frame_up',
        label='Structural frame up - building available for envelope',
        producer_stage='superstructure',
        consumer_stages=('envelope',),
        kind='predecessor_stage',
        producer_activities=(('frag.superstructure.steel', 'b50'),),
        why=(
            'INTRODUCED FOR REVIEW. Cladding and blockwork hang off the frame, so the frame must '
            'be up first - but "the frame" means the steel and the slab, not the whole '
            'superstructure package. The last activity in that package is fireproofing to steel, '
            'which is interior work that in practice runs while the envelope is being clad. '
            'Gating envelope on the composite slab pour rather than on fireproofing takes the '
            'artificial 32 days out of the chain that whole-stage finish-to-start was charging '
            'for work the envelope does not wait on.'
        ),
    ),
    GateRule(
        id='superstructure_complete',
        label='Superstructure complete - frame available for MEP',
        producer_stage='superstructure',
        consumer_stages=('mep_power', 'mep_cooling'),
        kind='predecessor_stage',
        why=(
            'INTRODUCED FOR REVIEW. Cladding and blockwork hang off the frame, and MEP first fix '
            'is installed into it - containment, cable tray, pipework and plant plinths all need '
            'structure to fix to. Without this the transformer and chiller installations began '
            'on the same day as the foundations.\n'
            '\n'
            'This one costs schedule, which is the point: mep_power can no longer start until '
            'the frame is up, and on a 12 MW N+1 brief that is what makes the power train the '
            'critical path rather than an artefact of everything starting at once.\n'
            '\n'
            'Conservative in the same way as substructure_complete: envelope normally follows '
            'the erection gang around the building rather than waiting for topping out, MEP '
            'first fix commonly starts in the zones already framed, and fireproofing to steel '
            'often overlaps the envelope entirely. NOT MODELLED: the part of MEP that genuinely '
            'is independent of the frame - external substation and yard works, and the off-site '
            'manufacture the delivery gates already cover - is held back here with the rest, so '
            'this overstates the constraint on those.'
        ),
    ),
    GateRule(
        id='envelope_weathertight',
        label='Building weather-tight - halls available for fit-out',
        producer_stage='envelope',
        consumer_stages=('fit_out',),
        kind='predecessor_stage',
        producer_activities=(
            ('frag.envelope.shell', 'd20'),  # roof waterproofing and insulation
            ('frag.envelope.shell', 'd30'),  # external cladding and rainscreen
        ),
        release_per_zone_kind='data_hall',
        why=(
            'INTRODUCED FOR REVIEW. Raised floor, containment and structured cabling do not go '
            'into a building open to the weather - but what fit-out waits for is a ROOFED AND '
            'CLAD hall, not the envelope package finished. The previous version waited for '
            'fire-stopping to penetrations and water-tightness close-out as well, which added '
            '50 days that no hall actually waits for, and made fit-out the false long pole of '
            'the whole programme.\n'
            '\n'
            'STILL AN APPROXIMATION. A real job encloses one hall and starts fitting it out '
            'while the next is still being clad; this gates ALL fit-out on the LAST hall being '
            'roofed and clad, because the fragnets are whole-building and there is no per-zone '
            'sub-network to hang a per-hall release off. So it remains conservative, just no '
            'longer conservative about the wrong thing.'
        ),
    ),
    GateRule(
        id='fire_installed',
        label='Fire detection and suppression installed - ready for commissioning',
        producer_stage='fire_bms',
        consumer_stages=('commissioning',),
        kind='readiness',
        why=(
            'INTRODUCED FOR REVIEW. The counterpart to power_installed and cooling_installed, '
            'and the more serious omission of the three: an L5 integrated systems test runs the '
            'facility under load, and running a data hall under load with no detection or '
            'suppression in place is not something a commissioning agent would sign. '
            'DOMAIN_KNOWLEDGE.md §5 also ties occupancy to the final fire NOC.'
        ),
    ),
    GateRule(
        id='fit_out_complete',
        label='Fit-out complete - halls ready for commissioning',
        producer_stage='fit_out',
        consumer_stages=('commissioning',),
        kind='readiness',
        why=(
            'INTRODUCED FOR REVIEW. L4/L5 commissioning exercises the halls as they will be '
            'operated, which presupposes the containment, racks and structured cabling are in. '
            'Without this the schedule allowed integrated systems testing to complete in a hall '
            'that had no cabling in it.'
        ),
    ),
    GateRule(
        id='power_installed',
        label='Power train installed - ready for commissioning',
        producer_stage='mep_power',
        consumer_stages=('commissioning',),
        kind='readiness',
        why=(
            'INTRODUCED FOR REVIEW. DOMAIN_KNOWLEDGE.md §4 runs commissioning as an L1-L5 '
            'ladder over installed plant, so it cannot precede installation. Without this rule '
            'the schedule had commissioning finishing three hundred days BEFORE the power '
            'train it commissions was installed - an incoherence the forward pass made visible '
            'and nothing else would have caught.'
        ),
    ),
    GateRule(
        id='cooling_installed',
        label='Cooling plant installed - ready for commissioning',
        producer_stage='mep_cooling',
        consumer_stages=('commissioning',),
        kind='readiness',
        why=(
            'INTRODUCED FOR REVIEW. The counterpart to power_installed: integrated systems '
            'testing exercises cooling under load, so the cooling plant must exist first.'
        ),
    ),
)

#: What a city-pathway entry's `blocks` token points at, when it is not a stage name.
#:
#: The pathway library blocks a mixture of stages ('substructure', 'handover') and finer things
#: ('energisation', 'commissioning_l4'). The stage names resolve themselves; these do not, and
#: before this table they resolved to nothing at all - the entries were reported as metadata and
#: constrained no activity. Each row says which library activity the approval actually holds up.
#:
#: ('fragnet', fragnet_id, activity_id) targets one instanced activity.
#: ('statutory', pathway_id)            targets another approval, so approvals can chain.
PATHWAY_BLOCK_ALIASES: Dict[str, Tuple[Tuple[str, ...], ...]] = {
    # CEIG clears the installation for live HV work. DOMAIN_KNOWLEDGE.md section 5:
    # "Energisation cannot precede CEIG approval."
    'energisation': (('fragnet', 'frag.mep.power_train', 'c70'),),
    # And the commissioning levels that exercise the facility energised.
    'commissioning_l4': (('fragnet', 'frag.commissioning.ladder', 'e40'),),
    'commissioning_l5': (
        ('fragnet', 'frag.commissioning.ladder', 'e50'),
        ('fragnet', 'frag.commissioning.ladder', 'e60'),
    ),
    # The final fire NOC gates the occupancy certificate, not the whole handover stage:
    # "occupancy cannot precede the final fire NOC" (section 5). Approval chains to approval.
    'occupancy': (('statutory', 'path.nm.occupancy_certificate'),),
    # PESO licenses BULK DIESEL STORAGE. Its library entry blocked the whole commissioning
    # stage, so it gated L1 factory acceptance testing - which has nothing to do with diesel.
    # Narrowed to the genset and fuel systems it actually licenses.
    'genset_fuel_systems': (
        ('fragnet', 'frag.mep.power_train', 'c65'),  # diesel generator set installation
        ('fragnet', 'frag.mep.power_train', 'c67'),  # bulk HSD storage and fuel system
    ),
}


#: When an approval can be APPLIED FOR, as (fragnet_id, activity_id).
#:
#: The pathway library records how long an approval takes and what it blocks. It does not record
#: when it is lodged, and modelling every approval as starting on day zero made the late ones
#: inert: CEIG is eight weeks, so from day zero it cleared on day 56 against an energisation on
#: day 520 - a hard edge in the graph that could never bind. The approval was expressed and
#: meaningless, which is worse than absent because it looks handled.
#:
#: An approval that inspects installed work cannot be lodged before that work exists. This says
#: which activity has to be far enough along, and the approval's duration then runs from there,
#: so the eight weeks land where they actually fall.
#:
#: Approvals NOT listed here keep the day-zero start, which is right for the ones lodged off
#: drawings at the outset - environmental clearance, consent to establish, building sanction.
PATHWAY_LODGEMENT_AFTER: Dict[str, Tuple[str, str]] = {
    # The electrical inspector inspects an installation. Lodged once the HV/MV switchgear is in
    # - drawings go with the application and the inspection follows completion, so this is the
    # application point rather than the inspection point.
    'path.nm.ceig_energisation': ('frag.mep.power_train', 'c30'),
    # Occupancy is applied for against a building that is finished and commissioned.
    'path.nm.occupancy_certificate': ('frag.commissioning.ladder', 'e50'),
    # The final fire NOC follows the fire systems being tested, not the start of the job.
    'path.nm.fire_noc_final': ('frag.fire_bms.detection_suppression', 'e60'),
}


#: Delivery gates are not listed above because there is one per long-lead item actually selected;
#: they are generated from the fragnets' material_links. This rule carries their shared metadata.
DELIVERY_GATE = GateRule(
    id='delivery',
    label='Delivery to site',
    producer_stage='procurement',
    consumer_stages=(),  # resolved per material link
    kind='delivery',
    why=(
        'Long-lead gear usually drives RFS (DOMAIN_KNOWLEDGE.md §4): "Front-load it; tie '
        'construction to delivery." The installing activity cannot start before the plant '
        'arrives, so delivery is a hard predecessor rather than an assumption.'
    ),
    per_material_link=True,
)


def material_link_index(fragnets: Sequence[Dict]) -> Dict[str, List[Tuple[str, str]]]:
    """lead_id -> [(fragnet_id, activity_id), ...] for every declared material link.

    Read straight from library data, so a fragnet added later is picked up with no code change.
    Sorted for deterministic assembly.
    """
    index: Dict[str, List[Tuple[str, str]]] = {}
    for frag in fragnets:
        for link in frag.get('material_links', []) or []:
            lead = link.get('requires_delivery_of')
            activity = link.get('activity')
            if lead and activity:
                index.setdefault(lead, []).append((frag['id'], activity))
    return {lead: sorted(pairs) for lead, pairs in sorted(index.items())}


def delivery_gate_id(lead_id: str) -> str:
    return gate_id(f'delivery.{lead_id}')


def cross_stage_gate_id(rule: GateRule) -> str:
    return gate_id(rule.id)


def statutory_id(pathway_id: str) -> str:
    """Id for the activity an approval becomes. Distinct prefix so it is greppable in a plan."""
    return f'stat.{slug(pathway_id)}'
