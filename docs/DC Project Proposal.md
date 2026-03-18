# Proposal

**Distributed Computing Project Proposal**  
Pallavi Desai, Eric Nelson

**Problem Statement and Background**  
For our final project, we will study how causal consistency mechanisms behave under frequent membership changes in distributed systems. Causal consistency is a widely used correctness model, commonly implemented using vector clocks or related metadata structures. While effective under stable membership, conventional vector-clock-based mechanisms can scale poorly in high-churn environments due to their O(n) metadata size and accumulation of obsolete entries for departed nodes. Frequent joins and leaves can cause causal metadata to retain obsolete entries, inflate message sizes, and introduce delivery delays from unnecessary dependency tracking.  
These issues are increasingly relevant in modern systems such as auto-scaling cloud services and geo-distributed deployments, where node membership can change rapidly in response to workload and demand. This project aims to empirically analyze how causal metadata grows and impacts performance under sustained membership churn, and to evaluate whether principled techniques, such as metadata pruning, garbage collection, or Dotted Version Vector clocks, can reduce overhead while preserving causal guarantees. If time permits, we would like to explore associating each vector entry with a lease, and automatically expire causal dependencies when the lease is not renewed, reducing stale metadata from departed nodes.

**Proposed Approach**  
We will build a Python-based event simulation using a tool like SimPy to model a distributed key-value store with hundreds of nodes under varying churn loads. First, we will compare Dotted Version Vectors (DVV) to standard vector clocks. DVV improves upon standard vector clocks by separating discrete events, or dots, from the broader causal context, “summary vector”, which prevents metadata bloat during concurrent updates. Second, if time permits, we will develop our proposed Lease-Based DVV. This method extends DVV by assigning an expiration time to each entry in the summary vector. If a node does not heartbeat within the lease window, its entry is pruned from the summary vector via a garbage collector. This introduces time-bounded causal tracking in that after a lease expiry, we no longer guarantee tracking dependencies involving that node, and we trade completeness of causality for reduced metadata in the presence of churn. We will analyze this tradeoff of causal guarantees and churn bloat in our report.

We also hope to leverage the code here to get us started with our node implementation:

* [UCSC \- Distributed kv-store](https://github.com/jasoncbad/Distributed-KV-Store)  
* [DVV implementation](https://github.com/ricardobcl/Dotted-Version-Vectors)

**Expected Contributions (Novelty)**   
Our contributions will come in three parts:

1. Simulation framework to benchmark different causal clocks under dynamic membership. This will include simulating stable, low churn, sustained churn, and burst churn environments.  
2. Empirical analysis, comparing vector clocks, DVV, and DVV \+ lease:  
   1. Metadata per message  
   2. Metadata growth vs churn  
   3. Latency statistics   
   4. Dependency queue length (if relevant)  
   5. Causal violations (for lease method)  
3. Implementation and comparison of a lease-based pruning for causal integrity within a fixed time window for high churn environments.

We expect to demonstrate that while DVV handles high concurrency better than traditional vector clocks, our lease extension significantly reduces the metadata footprint during high-churn periods. The final deliverable will be a validated proof-of-concept and a reusable SimPy framework for testing consistency protocols.

**References**

1. Preguiça, N. M., Baquero, C., Almeida, P. S., Fonte, V., & Gonçalves, R. (2010). Dotted version vectors: Logical clocks for optimistic replication. CoRR, abs/1011.5808.  
2. Kulkarni, S. S., Demirbas, M., Madeppa, D., Avva, B., & Leone, M. (2014). *Logical physical clocks and consistent snapshots in globally distributed databases*. In Proceedings of the 18th International Conference on Principles of Distributed Systems (OPODIS 2014), Lecture Notes in Computer Science, vol. 8878 (pp. 17–32). Springer.

# Ideas

**2/24/26 Questions for the Professor:** 

- Are there any simulators that already exist to run our project or do we need to build it from scratch?   
- Are there any papers where people have done a similar thing to what we have in mind?   
- What level of depth do we need for the initial experiments?   
* Build what we need, NS2 is a more complicated simulator. Do it such that it captures the failures we care about.   
* Eric has the notes from this conversation   
- How do we verify causality for vector clocks?  
* What “correctness” metrics are there?  
* Do the metrics we have look good or should we have something different?   
  * Metadata per message  
  * Metadata growth vs churn  
  * Latency statistics   
  * Dependency queue length (if relevant)  
  * Causal violations (for lease method)  
  * He wants the upper level application to follow some pattern, show the pattern versus our results.   
- What format should the midterm report be?  
* Doesn’t have to be formal   
- What are the applications of vector clocks?  
- Does the Mind, Matter, Machines talk on AI and consciousness count for the extra credit opportunity? 

**Ideas:** 

1. Create a Twitter-like service that emphasizes causal consistency across replicas and compare it to eventual and linearizable versions.   
* Implement per-client session tracking and vector-clock or dotted-version metadata.   
* Use APIs where reads can ask for no eventual guarantees, causal, or linearizable (through a central coordinator or Raft group).   
* Implement a test harness that does reordering, delays, and partitions.   
* We are trying to quantify the overhead of causal consistency vs. eventual consistency and measure how often users see anomalies under different models.   
* We would need to keep the app simple, i.e. no auth, UI, just json APIs so it’s doable within the scope of the class. 

Feedback from Professor: Add something novel even if it’s small to this idea. You can add something to something else that already exists. It’s more about doing the novel thing than building a whole system. The tweak should be interesting and novel. You could tweak the algorithm or the situation, i.e. new failure mode. Modifying Raft also works. 

Potential repos for this idea: [UCSC project](https://github.com/jasoncbad/Distributed-KV-Store) and [CS 425 project](https://github.com/J0Nreynolds/cs425-mp2?tab=readme-ov-file). 

**Novelty Ideas:** 

- Using the CS 425 repo, add functionality to automatically choose between eventual, causal, and linearizable based on observed access patterns or anomaly risk.   
* We can add a causal mode by layering vector clocks for per-key operations.   
* Track frequency of read-write races, cross-client dependencies, or “reply-before-tweet” anomalies in the Twitter-like environment.   
* We can promote keys or clients to a stronger consistency (linearizable) when anomalies are detected and demote them when stable.   
* The novelty is in characterizing how often we really need strong consistency for realistic social-graph-like workloads. And to show an automated scheme that keeps most keys cheap (eventual/causal) but selectively strengthens consistency where it matters.   
- Using the UCSC project repo which supports view changes and dynamic node addition, we can include re-partitioning and re-distribution of keys.   
* We can examine causal consistency during frequent membership changes by implementing controlled join/leave patterns. We can also track how vector-clock sizes, metadata propagation, and delay queues behave when membership churns.   
* We can also optimize bounded vector clocks or per-shared logical clocks and garbage collection of obsolete dependencies after view changes.   
* The novelty is showing concrete algorithms and measurements for maintaining causal guarantees while scaling up and down. We can also compare a naive implementation to an optimized one that shrinks metadata. 

Raft papers: [https://www.sciencedirect.com/science/article/pii/S1084804525000086](https://www.sciencedirect.com/science/article/pii/S1084804525000086)

2. Build a small RPC service that supports several replication/serving strategies and quantifies tail latency behavior.   
* Implement a single logical service, i.e. profile lookup with primary backup, quorum reads, client-side hedged requests, and client-side retries.   
* Add synthetic variability like random server slowdowns, node failures, and network delays.   
* Compare strategies under light vs. heavy load, low vs. high variance service time.   
* Measure mean vs. tail latency, extra load generated by hedging, impact on overall throughput. 

Also ask where the project proposal guidelines are. Something like this page should work. Team, broad idea, which existing software we want to use, what our plan is to add something novel. 

problem statement

* Vector clocks scale poorly with high churn due to O(n) vector and metadata size  
* We would like to investigate alternatives like pruning, gc, and HLC for a direct comparison

background/motivation 

* Lamport clocks establish basic causality, vector clocks extend this to concurrent operations  
* Version Vectors \- refine vector clocks for eventual consistency (better for replicas)  
* Dotted version vectors \- avoids tracking every process  
* Hybrid Logical Clocks HLC \- blend physical time and logical sime for looser causal constraints but better real world scalability  
* Recent trends toward decentralized services and ephemeral / serverless compute  
* Vector clock causal consistency mechanisms were designed for stable memberships, but are now routinely deployed in systems where membership churn is continuous and expected like autoscaling cloud services and geo-distributed deployments.

proposed approach

* Different churn  
  * Stable membership  
  * Low churn  
  * Sustained churn  
  * Bursty churn

expected contributions/outcomes

* Analysis and comparison of vector clock vs newer variants  
  * Clock size  
  * Metadata per message  
  * Metadata growth vs churn  
  * Latency statistics  
  * Dependency queue length  
  * CPU overhead  
  * Causal violations

# Tasking

Our contributions will come in three parts:

1. Simulation framework to benchmark different causal clocks under dynamic membership. This will include simulating stable, low churn, sustained churn, and burst churn environments.  
2. Empirical analysis, comparing vector clocks, DVV, and DVV \+ lease:  
   1. Metadata per message  
   2. Metadata growth vs churn  
   3. Latency statistics   
   4. Dependency queue length (if relevant)  
   5. Causal violations (for lease method)  
3. Implementation and comparison of a lease-based pruning for causal integrity within a fixed time window for high churn environments.

**High Level TODOs**

* Simulation Environment (implement or find) \- both   
  * The ability to add and remove nodes  
  * Send messages w/ delay  
  * Maybe focus on just updating the clocks instead of updating the KV store   
* Metrics (research) \- Pallavi   
  * **How do we confirm casual consistency?**  
    * We confirm this by checking that every read in our simulated history respects the happens-before relation that the clocks encode, and explicitly counts it when it doesn’t. Here are the steps:   
1. Define the target precisely, i.e. for every read, the set of versions it has observed is closed under causality: it never sees an effect B without also having seen all causally prior effects A.   
2. Use the clocks to compute happens-before, so for the vector-clock baseline, implement the usual partial order: X \-\> Y iff X\[i\] ≤ Y\[i\] for all i with at least one strict \<. For DVV, each update carries a dot (the new event), a summary vector (its causal past), and an event e1 causally precedes e2 when e1’s dot lies in e2’s causal past.   
3. Offline trace checker for the baseline so that after each run, we can verify that the baseline algorithms (vector clocks and unleashed DVV) actually enforce causal consistency. For each read event that returns values produced by write B, check: for every write A such that A \-\> B, the read’s local context includes A (i.e. its metadata dominates or includes A’s metadata). If any such A is missing, you’ve found a causal violation, which should be 0 for baseline algorithms.   
4. Measuring and confirming violations under leases, we need to confirm a few things. To extend our logger, when a read or update processes metadata that referenced a pruned entry (expired lease), mark this as a potential causal violation and record it with the event ID and clocks. Then run the same offline checker and count how many reads violate the baseline property. Report this as the “causal violation rate” along with metadata size and latency metrics, similar to prior work on approximate or relaxed causal consistency.   
   * Are there similar papers studying membership churn and casual consistency? What do they measure? We should try to find a similar paper to model   
     * [Evaluating DVVs in Riak](https://asc.di.fct.unl.pt/~nmp/pubs/inforum-2011-2.pdf) \- it directly supports our idea that DVV reduces metadata bloat compared to standard VCs in realistic systems.   
     * [Concise Server-Wide Causality Management](https://repositorium.uminho.pt/server/api/core/bitstreams/2723af1f-b955-4996-91e3-e2a392da38f9/content) \- optimizing causality metadata for eventually consistent stores. Conceptually close to our garbage collection method.   
     * [Performance of Approximate Causal Consistency](https://www.cs.uic.edu/~ajayk/ext/p7-hsu.pdf) \- their metrics are very similar to ours so it’s a close tradeoff to what we’re looking at.   
     * There were a few others but I think these are the most relevant.   
   * Pass the metrics we plan to collect by the professor \- questions are in the Ideas tab   
   * Try to get this done by Tuesday so we can ask the prof   
* Implement baseline vector clocks \- Eric  
  * DVV and vanilla vector clock  
    * Identify repos for each  
    * Detail usage and assumptions  
  * Are we using the DVV Github repo as it is or re-implementing a simplified version of that?   
  * For lease-based DVV, what is the lease unit (i.e. simulation time, heartbeat count) and where is the lease state stored?   
* Our approach (lease based vector clocks)  
  * Is this novel enough?  
  * What implementations exist that we can leverage?  
  * How do we handle nodes that rejoin?  
  * What assumptions does our model work under?

**March 1-7th Tasking**  
Main goal: Basic simulation with (meta-data / time, latency / clock size, meta-data / churn rate ) metrics on vanilla vector clocks

* Basic simulation with vector clock updates   
  * Ability to measure   
* Metrics research \- Pallavi \- see above   
* Finish research on existing repos, code \- Eric  
  * Existing implementation of vectors clocks \+ advanced vector clocks  
* Setup python repo \- Eric  
* Choose simulation requirements and simulator \- Both, sync mid-week \- see below   
* Meet with professor (aiming for Tuesday, could also do Thursday) ✅

Notes for the proposal from class: include what we are working on, central idea, what is new (novel), what have we done to validate it so far. A bad grade would come if you haven’t done any work since the initial proposal. 

The Code tab of this document has some starter code that Claude generated. I am not sure how well it works but it’s a start. I also pasted just the code into that document so you can directly copy paste it into VS Code. 
 
**Skeleton Steps to get Started:** 

Implement a tiny SimPy-based “cluster harness” that can run a handful of abstract nodes and churn events, with no real KV or causal clocks yet.

Specifically:

1. **Set up the SimPy environment and node abstraction.**  
   * Create a `Node` class with an `id`, a `status` (up/down), and a `process(env)` generator that loops, sends “dummy messages” at some rate, and yields `env.timeout(...)`.  
   * In `main`, create `env = simpy.Environment()` and a list of nodes, and start each with `env.process(node.process(env))`.  
2. **Add membership churn events.**  
   * Define separate processes like `churn(env)` that periodically join/leave nodes by flipping their `status` or creating/removing them from the cluster list, again using `yield env.timeout(...)`.  
   * Run `env.run(until=SIM_TIME)` and log simple stats: number of nodes over time, join/leave counts.  
3. **Freeze the interfaces for clocks.**  
   * Decide and code the *shape* of your causal metadata API (e.g., `clock.local_event()`, `clock.send() -> metadata`, `clock.recv(metadata)`) but initially implement them as no-ops or trivial counters.  
   * Make sure your `Node` uses these APIs where messages would be sent, so you can later plug in “vector clock”, “DVV”, and “Lease-DVV” implementations behind the same interface.

Once this skeleton runs and you can see nodes coming and going under different churn patterns, you’ll be in a good position to: (a) drop in a simple vector clock implementation, (b) then wire in the DVV repo code, and (c) finally add leasing and metrics (metadata size, latency, etc.) without reworking the simulation structure.

**Simulation Requirements and Simulator:** 

We should use SimPy. The requirements should be these: 

1. **Node and network modeling**  
   * Represent each node as a SimPy process with: local KV state, causal clock (VC, DVV, Lease-DVV), membership status (joined/left), and a message handler loop.grotto-networking+1  
   * A simple network abstraction (e.g., a router process or per-link queues) that can add configurable latency, jitter, and optional loss/duplication for robustness experiments.\[[grotto-networking](https://www.grotto-networking.com/DiscreteEventPython.html)\]​  
2. **Membership churn controls**  
   * Parameterized churn generators that can produce:  
     * Stable regime (no churn), low churn, sustained churn (Poisson join/leave), and burst churn episodes.\[[grotto-networking](https://www.grotto-networking.com/DiscreteEventPython.html)\]​  
   * Ability to choose whether node IDs are reused or always new (affects metadata pruning and obsolescence).  
3. **Workload generation**  
   * Configurable clients (or node-local workloads) issuing GET/PUT operations with tunable:  
     * Read/write ratio, arrival process (e.g., Poisson), key distribution (uniform vs skewed), and concurrency level.\[[grotto-networking](https://www.grotto-networking.com/DiscreteEventPython.html)\]​  
   * Support for multi-key causal chains (e.g., write A then write B that causally depends on A) to meaningfully exercise the causal clocks.  
4. **Clock pluggability**  
   * A **clock interface** that all algorithms implement (VC, DVV, Lease-DVV), e.g.:  
     * `local_event()`, `prepare_send() -> metadata`, `on_receive(metadata)`, `compare_versions(v1, v2)`.  
   * The node logic must be agnostic to which clock implementation is plugged in, so you can run the same scenarios under all three.  
5. **Measurement and logging**  
   * Per-message metrics: serialized metadata size (e.g., bytes of JSON or number of entries), message latency, sender/receiver IDs, churn state at send/receive.  
   * Per-node metrics: queue lengths (dependency/buffer queues), number of buffered messages due to unsatisfied dependencies, time to deliver updates.  
   * Global metrics:  
     * Metadata size distribution and growth over time vs churn regime.  
     * Latency distributions (end-to-end, replication) and throughput.  
     * Causal violation counts/rates for the Lease-DVV configuration, using the checker we discussed earlier.  
6. **Experimental control**  
   * Ability to fix a random seed for reproducibility and to run multiple seeds per configuration.  
   * A configuration layer (YAML/JSON or Python dicts) where you can define: cluster size, churn parameters, workload parameters, clock type, lease parameters, and run duration.  
   * Output in CSV/Parquet/JSON for downstream analysis and plotting in Python.