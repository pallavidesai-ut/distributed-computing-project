# Adaptive Lease-Based Causal Pruning in Distributed Key-Value Stores

Eric Nelson and Pallavi Desai  
Department of Computer Science, The University of Texas at Austin

## Abstract

Causal metadata is the hidden scaling cost of optimistic replication. Version
vectors (VV) give exact causality by storing one counter per actor, but their
cost grows with the actor namespace represented in an object's history. Dotted
version vectors (DVV) separate a version's newest event from its causal context,
which preserves exact ancestry while often reducing metadata for replicated
objects with concurrent updates. Interval tree clocks (ITC) avoid fixed global
actor identifiers, but their encoded identities can fragment under asymmetric
fork/join patterns. Lease-pruned DVV deliberately abandons exactness: it bounds
metadata by expiring old causal evidence, trading ancestry recall for smaller
version payloads and more stale siblings.

This report proposes an ideal simulator and experimental plan for a graduate
course study of these tradeoffs. The central design principle is to evaluate
metadata size and semantic behavior against an explicit ground-truth event DAG.
The simulator should report not only message bytes, but also whether each clock
correctly classifies ancestry and concurrency. The best final report should use
a small set of controlled experiments to make the clock differences visually
obvious: metadata versus lifetime actor count, correctness under concurrent
writes, lease duration Pareto frontiers, churn time series, and actor-domain
sensitivity. Exact clocks should preserve ancestry precision and recall over
their selected actor domain. Lease-based pruning should form a clear knob: more
pruning reduces metadata but lowers recall and increases stale-sibling pressure.
The result is not a claim that one clock dominates, but a precise map of when
each representation is the right engineering choice.

## 1. Introduction

Leaderless replicated key-value stores commonly allow any replica to accept a
write. When two writes are concurrent, the system should retain both versions as
siblings. When one write causally descends from another, the older version can
be safely discarded. Logical clocks provide the compact per-version metadata
used to make this decision without transmitting full histories.

The classical solution is a vector clock or version vector: each actor has a
counter, and vectors are compared componentwise. This representation is exact,
but its space cost is proportional to the number of actors appearing in the
causal history. That actor set is not necessarily the current cluster size. In
an elastic system, actors may be physical nodes, stable vnode slots, client
sessions, or dynamically allocated identities. A high-churn system with
short-lived physical node actors can accumulate metadata for many departed
nodes, even if only a small number are active at any time.

Dotted version vectors were designed for optimistic replication. A DVV stores a
single newest event, the dot, separately from the causal context observed by the
write. This distinction is elegant because it lets the system represent an
individual update without treating the entire actor summary as the update
itself. DVV remains exact when its context is retained. It should not be framed
as approximate or lossy.

Interval tree clocks attack a different problem: actor identity allocation. They
replace fixed actor names with tree-structured identities that can fork and
join. This can make dynamic membership natural, but it introduces a new cost:
identifier structure can grow under unbalanced churn. ITC should therefore be
measured on both metadata size and identity fragmentation.

Lease-pruned DVV is intentionally approximate. It starts from DVV and expires
old causal evidence after a lease window. This gives a useful systems knob:
short leases bound metadata, while long leases approach exact DVV. The cost is
semantic. After pruning, a later version may no longer prove that it descends
from an older version, so the store keeps obsolete siblings. In the worst case,
coarse or incorrect pruning can also collapse true conflicts.

The final report should answer four questions:

1. How does metadata scale with lifetime actor count, active membership, and
   object contention?
2. Which clocks preserve exact ancestry and concurrency under the same actor
   domain?
3. How do DVV and ITC differ in what they optimize: event representation versus
   dynamic identity allocation?
4. What Pareto frontier does lease-DVV offer between metadata reduction and
   semantic loss?

## 2. Clock Models and Expected Properties

The report should compare clocks as representations of the same ground-truth
causal DAG. The chosen actor domain must be explicit in every experiment.
Physical actors model churn-created node identities. Slot actors model stable
replica or vnode identities. Client actors model session-level causality.

| Clock | Exact over selected actor domain? | Metadata intuition | Primary strength | Primary risk |
| --- | --- | --- | --- | --- |
| Version Vector (VV) | Yes | One counter per actor in object history | Simple exact baseline | Metadata grows with actor cardinality |
| Dotted Version Vector (DVV) | Yes | Causal summary plus one event dot | Exact optimistic-replication metadata | Can still carry large contexts under churn |
| Interval Tree Clock (ITC) | Yes when implemented faithfully | Tree identity plus event state | Dynamic identity without global actor names | Identity fragmentation under asymmetric churn |
| Lease-DVV | No | DVV with expired evidence removed | Tunable bounded metadata | Recall loss and stale siblings |
| Membership-Lease-DVV | No | Retain active actors, expire departed actors | Production-style pruning policy | Depends on membership accuracy |

Three interpretation rules are essential. First, exact VV is not lossy. It is
large when the selected actor namespace is large. Second, exact DVV is not lossy.
If DVV loses ancestry in an exact experiment, the implementation or metric is
wrong. Third, lease-DVV is valuable precisely because it is approximate; the
report should evaluate the tradeoff rather than hide it.

## 3. Ideal Simulator Design

The simulator should be a discrete-event replicated key-value store, but the
core research instrument is the ground-truth causal DAG. Every write creates an
event ID. A version's true history is the transitive closure of the versions it
read plus its own event. Clock metadata is generated separately and is the only
information the store uses for conflict decisions. Evaluation compares clock
claims against the true DAG.

Each version record should contain:

| Field | Purpose |
| --- | --- |
| `key` | Object key, with `k0` reserved as the hot key |
| `event_id` | Unique ground-truth event identifier |
| `actor_id` | Actor that issues the write under the selected actor domain |
| `clock_payload` | VV, DVV, ITC, or lease-DVV metadata |
| `read_set` | Versions observed by the write |
| `true_history` | Full causal event set for evaluation only |
| `created_at` | Simulated timestamp |
| `membership_epoch` | Active membership view when the write was created |

On every version comparison, the simulator records both the true relation and
the clock relation:

| Relation | Meaning |
| --- | --- |
| `incoming_descends_existing` | Incoming version should replace existing version |
| `existing_descends_incoming` | Incoming version should be dropped |
| `concurrent` | Both versions should be retained as siblings |
| `incomparable_due_to_pruning` | Clock cannot prove true ancestry because evidence was forgotten |

The primary semantic metrics are:

| Metric | Definition | Desired behavior |
| --- | --- | --- |
| Ancestry precision | Fraction of represented causal events that are truly causal | Exact clocks equal 1.0 |
| Ancestry recall | Fraction of true causal events represented by the clock | Exact clocks equal 1.0 |
| Missed-conflict rate | True concurrent pairs incorrectly ordered by the clock | Should be 0.0 for exact clocks and near 0.0 for safe pruning |
| Stale-sibling rate | True ancestor/descendant pairs retained as concurrent | Increases as pruning becomes aggressive |
| Sibling amplification | Stored siblings relative to exact DVV | Shows application-visible cleanup cost |

The primary cost metrics are:

| Metric | Definition |
| --- | --- |
| Average metadata bytes | Mean serialized clock payload bytes per version |
| P95 metadata bytes | 95th percentile serialized clock payload bytes per version |
| Actor entries | Count of actor-like entries in the payload |
| ITC identity bits | Number of bits or encoded symbols used for ITC identity/state |
| Pruned evidence count | Number of causal entries removed by lease policy |

Metadata bytes should be reported as JSON bytes or another clearly specified
encoding. If JSON is used, the report must say these are relative simulator
bytes, not optimized wire-format bytes.

## 4. Experimental Design

A strong final report should not rely on one large simulation alone. It should
combine controlled DAG experiments, macro workload experiments, and ablations.
The controlled experiments make correctness and representation differences
unambiguous. The macro workload shows the systems consequence under churn.

### Experiment 1: Controlled causality laboratory

Purpose: prove that the simulator can distinguish ancestry from concurrency and
show the exact semantic behavior of each clock.

Construct deterministic event graphs:

| DAG case | Description | Expected exact-clock result |
| --- | --- | --- |
| Chain | `a1 -> a2 -> a3 -> ...` | All descendants dominate ancestors |
| Fork | One version read by two concurrent writers | Writers are concurrent siblings |
| Diamond merge | Two siblings read by a merge write | Merge dominates both siblings |
| Same-actor concurrent dots | Same coordinator issues distinct concurrent writes | DVV dots preserve event identity |
| Departed-actor ancestor | Ancestor created by actor that later leaves | Exact clocks retain ancestry; lease-DVV may forget |

Required table: `Table 2. Controlled DAG correctness`.

| Column | Label |
| --- | --- |
| DAG case | `DAG case` |
| Clock | `Clock` |
| Expected relation | `Expected relation` |
| Observed relation | `Observed relation` |
| Pass? | `Pass` |

This table should have all exact clocks passing. Lease-DVV should pass when the
lease window covers the relevant dependency and intentionally fail recall when
it does not.

### Experiment 2: Metadata scaling with actor cardinality

Purpose: elegantly demonstrate the central asymptotic difference.

Run a synthetic workload where a single hot object is updated by an increasing
number of lifetime actors while active membership is held constant. This
isolates lifetime actor count from current cluster size.

Parameter sweep:

| Parameter | Values |
| --- | --- |
| Lifetime actors touching hot key | `8, 16, 32, 64, 128, 256, 512` |
| Active actors at one time | Fixed, e.g. `16` |
| Writes per actor | Fixed, e.g. `4` |
| Merge probability | `0.35` |
| Clocks | `VV`, `DVV`, `ITC`, `Lease-DVV L=16s` |

Required figure: `Figure 1. Metadata scaling with lifetime actors`.

| Plot field | Specification |
| --- | --- |
| X-axis | `Lifetime actors touching object` |
| Y-axis | `Average metadata bytes per version` |
| Scale | Log2 x-axis recommended |
| Lines | `VV`, `DVV`, `ITC`, `Lease-DVV L=16s` |
| Error bars | 95% confidence interval or standard error across seeds |
| Expected shape | VV grows roughly linearly with actor count; DVV grows more slowly when context compacts; lease-DVV flattens after expiration; ITC depends on identity fragmentation |

A companion plot should use entries instead of bytes.

Required figure: `Figure 2. Logical entries versus serialized bytes`.

| Plot field | Specification |
| --- | --- |
| X-axis | `Lifetime actors touching object` |
| Left Y-axis | `Average actor/context entries per version` |
| Right Y-axis | `Average metadata bytes per version` |
| Lines or panels | One panel per clock |
| Expected insight | Logical entry count and serialized bytes can diverge because DVV and ITC have representation overhead |

### Experiment 3: Actor-domain sensitivity

Purpose: prevent misleading conclusions by showing that actor identity is a
modeling decision.

Run the same churn workload with three actor domains:

| Actor domain | Actor IDs represent | Expected conclusion |
| --- | --- | --- |
| `physical` | Churn-created nodes | Metadata reflects cumulative node churn |
| `slot` | Stable logical replica/vnode slots | Metadata is bounded by slot count |
| `client` | Client/session identities | Metadata reflects client cardinality and session causality |

Required figure: `Figure 3. Actor-domain sensitivity`.

| Plot field | Specification |
| --- | --- |
| X-axis | `Actor domain` |
| Y-axis | `Average metadata bytes per version` |
| Group/hue | `Clock` |
| Facets | `stable`, `sustained`, `burst` profiles |
| Expected insight | VV is not inherently bad; physical-domain churn is the case that exposes actor explosion |

Required table: `Table 3. Actor-domain interpretation`.

| Column | Label |
| --- | --- |
| Actor domain | `Actor domain` |
| Best-fit production analogy | `Production analogy` |
| Scaling variable | `Scaling variable` |
| Main risk | `Main risk` |

### Experiment 4: High-churn replicated workload

Purpose: show the systems-level result under an elastic replicated key-value
store.

Suggested main configuration:

| Parameter | Value |
| --- | --- |
| Churn profiles | `stable`, `low`, `sustained`, `burst` |
| Simulation time | At least `1000s` simulated time |
| Seeds | At least `5`, preferably `10` |
| Initial active nodes | `10` |
| Active membership range | `4` to `28` |
| Replication factor | `3` or `4`, fixed in main study |
| Keys | `12` total, one hot key |
| Hot-key probability | `0.60` to `0.70` |
| Burst writers | `4` or `8` |
| Merge probability | `0.25` to `0.40` |
| Clocks | `VV`, `DVV`, `ITC`, `Lease-DVV L=8/16/32s` |
| Main actor domain | `physical` |

Required figure: `Figure 4. Churn profile validation`.

| Plot field | Specification |
| --- | --- |
| X-axis | `Simulated time (s)` |
| Y-axis | `Active replicas` |
| Lines | `stable`, `low`, `sustained`, `burst` |
| Expected insight | Profiles produce visibly different membership dynamics |

Required figure: `Figure 5. Metadata by churn profile`.

| Plot field | Specification |
| --- | --- |
| X-axis | `Churn profile` |
| Y-axis | `Average metadata bytes per version` |
| Group/hue | `Clock` |
| Error bars | Standard error across seeds |
| Caption requirement | State serialization format, e.g. JSON bytes |

Required figure: `Figure 6. Tail metadata by churn profile`.

| Plot field | Specification |
| --- | --- |
| X-axis | `Churn profile` |
| Y-axis | `P95 metadata bytes per version` |
| Group/hue | `Clock` |
| Expected insight | Tail behavior is where actor explosion and burst churn are most visible |

Required table: `Table 4. Main aggregate results`.

| Column | Label |
| --- | --- |
| Profile | `Profile` |
| Clock | `Clock` |
| Avg metadata | `Avg metadata bytes` |
| P95 metadata | `P95 metadata bytes` |
| Actor entries | `Avg actor/context entries` |
| Precision | `Ancestry precision` |
| Recall | `Ancestry recall` |
| Missed conflicts | `Missed-conflict rate` |
| Stale siblings | `Stale-sibling rate` |
| Hot-key siblings | `Avg hot-key siblings` |

### Experiment 5: Semantic behavior under contention

Purpose: connect clock metadata to user-visible conflict handling.

Sweep the number of concurrent writers to the hot key while keeping actor count
fixed.

Parameter sweep:

| Parameter | Values |
| --- | --- |
| Concurrent writers per burst | `1, 2, 4, 8, 16` |
| Merge delay | Fixed, e.g. `10s` |
| Merge probability | Fixed, e.g. `0.35` |
| Actor domain | `physical` and optionally `client` |

Required figure: `Figure 7. Conflict handling under hot-key contention`.

| Plot field | Specification |
| --- | --- |
| X-axis | `Concurrent writers per burst` |
| Y-axis | `Average siblings for hot key` |
| Lines | `VV`, `DVV`, `ITC`, `Lease-DVV L=16s` |
| Expected insight | Exact clocks retain true siblings and clean up after merges; lease-DVV can retain stale siblings after pruning |

Required figure: `Figure 8. Semantic error decomposition`.

| Plot field | Specification |
| --- | --- |
| X-axis | `Clock` |
| Y-axis | `Decision error rate` |
| Stack segments | `Missed conflicts`, `Stale siblings` |
| Facets | `sustained`, `burst` |
| Expected insight | Safe pruning should produce stale siblings rather than missed conflicts |

### Experiment 6: Lease-DVV Pareto frontier

Purpose: make the approximate design choice explicit.

Sweep lease duration over a wide range. Include a no-pruning DVV baseline.

Parameter sweep:

| Parameter | Values |
| --- | --- |
| Lease duration | `4s, 8s, 16s, 32s, 64s, infinity` |
| Churn profiles | `low`, `sustained`, `burst` |
| Lease policy | Fixed lease and membership-aware lease if implemented |

Required figure: `Figure 9. Lease Pareto frontier`.

| Plot field | Specification |
| --- | --- |
| X-axis | `Metadata reduction vs exact DVV (%)` |
| Y-axis | `Ancestry recall loss vs exact DVV (%)` |
| Point labels | `L=4s`, `L=8s`, `L=16s`, `L=32s`, `L=64s` |
| Color | `Churn profile` |
| Shape | `Lease policy` |
| Expected insight | Short leases move right and up; membership-aware leases should dominate fixed leases if membership information is reliable |

Required figure: `Figure 10. Lease side effects`.

| Plot field | Specification |
| --- | --- |
| X-axis | `Lease duration (s)` |
| Y-axis | `Stale-sibling rate` |
| Lines | Churn profiles or lease policies |
| X-scale | Log2 recommended |
| Expected insight | Stale-sibling pressure decreases as lease duration increases |

Required table: `Table 5. Lease ablation`.

| Column | Label |
| --- | --- |
| Profile | `Profile` |
| Lease policy | `Lease policy` |
| Lease duration | `Lease duration (s)` |
| Avg metadata | `Avg metadata bytes` |
| Metadata reduction | `Metadata reduction vs DVV (%)` |
| Recall | `Ancestry recall` |
| Recall loss | `Recall loss vs DVV (%)` |
| Stale siblings | `Stale-sibling rate` |
| Pruned evidence | `Pruned evidence per write` |

### Experiment 7: ITC dynamic-identity stress test

Purpose: evaluate ITC on the problem it was designed to solve and the cost it
may pay.

Use a fork/join workload independent of the key-value simulation or embedded in
a client-domain workload. Vary churn symmetry.

Parameter sweep:

| Parameter | Values |
| --- | --- |
| Fork/join symmetry | `balanced`, `moderately asymmetric`, `highly asymmetric` |
| Churn cycles | `10, 25, 50, 100, 200` |
| Active actors | Fixed or bounded |

Required figure: `Figure 11. ITC identity fragmentation`.

| Plot field | Specification |
| --- | --- |
| X-axis | `Fork/join churn cycles` |
| Y-axis | `Average ITC encoded identity/state bytes` |
| Lines | `balanced`, `moderately asymmetric`, `highly asymmetric` |
| Optional comparison | `VV`, `DVV` under same actor-domain workload |
| Expected insight | ITC handles dynamic identity elegantly when churn is balanced, but encoded state can grow under asymmetric churn |

### Experiment 8: Time-series case study

Purpose: provide one narrative plot that readers can understand without reading
all aggregate tables.

Choose the sustained or burst profile and plot membership, metadata, and stale
siblings over time.

Required figure: `Figure 12. Burst-churn case study over time`.

| Panel | X-axis | Y-axis | Lines |
| --- | --- | --- | --- |
| A | `Simulated time (s)` | `Active replicas` | Membership count |
| B | `Simulated time (s)` | `Average metadata bytes per version` | Clock variants |
| C | `Simulated time (s)` | `Average hot-key siblings` | Clock variants |
| D | `Simulated time (s)` | `Cumulative pruned evidence` | Lease variants |

This plot is the best place to show causality between churn events, pruning,
metadata drops, and stale sibling accumulation.

## 5. Expected Results Narrative

A strong final report should present results in this order.

First, the controlled DAG experiment establishes trust. Exact VV, exact DVV, and
ITC should classify all chain, fork, and merge cases correctly. Lease-DVV should
match exact DVV when dependencies remain inside the lease window and lose recall
when dependencies expire. This validates that later aggregate differences are
semantic, not plotting artifacts.

Second, metadata scaling with lifetime actors should show the actor explosion
problem cleanly. With physical actors and a hot object touched by many departed
nodes, VV should grow with lifetime actor count. DVV should reduce metadata when
it can summarize context plus a dot more compactly than full per-version actor
vectors. ITC should show a different scaling curve: it may avoid explicit actor
names but can pay in encoded identity complexity. Lease-DVV should flatten after
old entries expire.

Third, actor-domain sensitivity should prevent overclaiming. If the actor domain
is stable slots, VV may be perfectly reasonable because the vector dimension is
bounded. If the domain is clients, metadata reflects client cardinality rather
than physical node churn. The final paper should explicitly say that actor
identity is part of the system design.

Fourth, the high-churn workload should show the systems-level tradeoff. Average
and P95 metadata should increase from stable to sustained or burst profiles for
exact clocks under physical actors. Exact clocks should keep precision and
recall at 1.0. Lease-DVV should reduce metadata but lower recall and increase
stale siblings as lease duration shrinks.

Fifth, the lease Pareto frontier should turn the proposed technique into an
engineering decision. The reader should be able to choose a lease duration by
looking at one plot: how many bytes are saved versus how much recall is lost.
Membership-aware lease-DVV should be presented as the more principled variant if
it retains active actors and prunes departed actors only after a grace period.

## 6. Threats to Validity

The most important threat is encoding. JSON metadata bytes are easy to inspect
and reproduce, but they are not optimized wire bytes. The report should use them
for relative comparison and include actor-entry counts so representation effects
are visible.

The second threat is workload realism. Synthetic hot-key contention and churn
profiles are chosen because they isolate causal behavior. A production workload
could have different key skew, write locality, read repair, anti-entropy, and
client session behavior.

The third threat is actor-domain modeling. Physical actors, slots, and clients
answer different questions. A report that claims vector clocks scale poorly
without specifying actor identity is imprecise. The main high-churn study should
use physical actors, but the final interpretation should include slot and client
sensitivity.

The fourth threat is lease policy. Fixed leases can prune quiet but still-active
actors. Membership-aware leases require reliable membership information. The
paper should compare them if possible or clearly state which one is used.

The fifth threat is ground truth. The simulator can retain full event histories
only because it is an evaluator. Real systems cannot. That is acceptable because
the ground-truth DAG is an oracle for measuring correctness loss.

## 7. Conclusion

The best final report should demonstrate three separations. The first is between
exactness and metadata cost: VV, DVV, and ITC can all be exact, but they encode
causality differently and pay different costs under churn. The second is between
actor domains: physical-node, slot, and client actors produce different scaling
stories. The third is between safety and cleanup: lease pruning should mostly
forget ancestry, creating stale siblings, rather than inventing order and
missing conflicts.

The expected contribution is an evaluation framework that makes these
separations visible. Controlled DAGs show correctness. Metadata scaling plots
show asymptotic behavior. Actor-domain sensitivity prevents misleading claims.
Contention experiments connect clocks to sibling behavior. Lease Pareto plots
show the central tradeoff. ITC stress tests explain dynamic identity costs. A
5-6 page final report built around these figures will read as a focused systems
study rather than an implementation diary.

## Final Figure and Table Checklist

| ID | Title | X-axis | Y-axis | Main encoding |
| --- | --- | --- | --- | --- |
| Figure 1 | Metadata scaling with lifetime actors | `Lifetime actors touching object` | `Average metadata bytes per version` | Line per clock |
| Figure 2 | Logical entries versus serialized bytes | `Lifetime actors touching object` | `Entries` and `Bytes` | Panel per clock |
| Figure 3 | Actor-domain sensitivity | `Actor domain` | `Average metadata bytes per version` | Clock hue, profile facets |
| Figure 4 | Churn profile validation | `Simulated time (s)` | `Active replicas` | Line per profile |
| Figure 5 | Metadata by churn profile | `Churn profile` | `Average metadata bytes per version` | Clock hue |
| Figure 6 | Tail metadata by churn profile | `Churn profile` | `P95 metadata bytes per version` | Clock hue |
| Figure 7 | Conflict handling under hot-key contention | `Concurrent writers per burst` | `Average siblings for hot key` | Line per clock |
| Figure 8 | Semantic error decomposition | `Clock` | `Decision error rate` | Stacked missed/stale errors |
| Figure 9 | Lease Pareto frontier | `Metadata reduction vs exact DVV (%)` | `Ancestry recall loss vs exact DVV (%)` | Labels by lease duration |
| Figure 10 | Lease side effects | `Lease duration (s)` | `Stale-sibling rate` | Line per profile or policy |
| Figure 11 | ITC identity fragmentation | `Fork/join churn cycles` | `Average ITC encoded identity/state bytes` | Line per symmetry level |
| Figure 12 | Burst-churn case study over time | `Simulated time (s)` | Multi-panel metrics | Clock lines plus membership |

| ID | Title | Purpose |
| --- | --- | --- |
| Table 1 | Clock models and expected properties | Defines exactness, metadata intuition, and risks |
| Table 2 | Controlled DAG correctness | Proves the evaluator and exact clocks are correct |
| Table 3 | Actor-domain interpretation | Separates physical, slot, and client conclusions |
| Table 4 | Main aggregate results | Reports cost and semantic metrics by profile and clock |
| Table 5 | Lease ablation | Reports metadata reduction, recall loss, and stale siblings by lease |

## References

[1] L. Lamport, "Time, clocks, and the ordering of events in a distributed
system," Communications of the ACM, 1978.

[2] C. J. Fidge, "Timestamps in message-passing systems that preserve the
partial ordering," Australian Computer Science Conference, 1988.

[3] F. Mattern, "Virtual time and global states of distributed systems," 1989.

[4] D. S. Parker et al., "Detection of mutual inconsistency in distributed
systems," IEEE Transactions on Software Engineering, 1983.

[5] G. DeCandia et al., "Dynamo: Amazon's highly available key-value store,"
SOSP, 2007.

[6] N. Preguica, C. Baquero, P. S. Almeida, V. Fonte, and R. Goncalves,
"Dotted version vectors: Logical clocks for optimistic replication," NETYS,
2012.

[7] P. S. Almeida, C. Baquero, and V. Fonte, "Interval tree clocks: A logical
clock for dynamic systems," OPODIS, 2008.

[8] R. S. Torres-Rojas and M. Ahamad, "Plausible clocks: constant size logical
clocks for distributed systems," Distributed Computing, 1999.
