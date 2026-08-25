# Env38 baseline summary

This machine-produced baseline integrates the accepted Phase 7F topology and Phase 7G geometry/orientation results; it does not recalculate metrics.

## Canonical identity

- Canonical run: `env38_20260825T150753632988Z_974f687c1ac4`
- Canonical SHA-256: `501E8B16A5F99143328166DB31DB159801C23268CEABBFCEC92A5A368C9A0D21`
- Scientific-content signature: `974F687C1AC4E1CDA548366DFB89FFBFCDBE05F44BA28342EF52149A4C607945`
- Topology signature: `4AF93910532EB430103267DAECF73B3ADC3B3D1599471EDF6B1632B6C5F898CB`

## Core metrics

| Family | Metric | Value | Units |
|---|---|---:|---|
| topology_controlled | Canonical nodes | 46 | nodes |
| topology_controlled | Physical canonical edges | 69 | physical streets |
| topology_controlled | Connected components | 1 | components |
| topology_controlled | Mean physical-node degree | 3 | physical streets per node |
| topology_controlled | Dead-end proportion | 0.217391304348 | proportion |
| topology_controlled | Degree-3 node proportion | 0.347826086957 | proportion |
| topology_controlled | Degree-4 node proportion | 0.434782608696 | proportion |
| topology_controlled | Cycle rank | 24 | independent cycles |
| geometry_weighted_network | Mean weighted shortest-path length | 721.425293868 | local units |
| geometric_morphology | Total physical network length | 11398.8543039 | local units |
| geometric_morphology | Mean physical-edge length | 165.200787014 | local units |
| geometric_morphology | Mean edge circuity | 1.03649758572 | ratio |
| geometric_morphology | Network circuity | 1.03884694539 | ratio |
| orientation_morphology | Chord orientation entropy | 2.9808051798 | nats |
| orientation_morphology | Normalized chord orientation entropy | 0.831809523264 | proportion |
| orientation_morphology | Segment-length-weighted orientation entropy | 3.22696251038 | nats |
| orientation_morphology | Normalized segment-length-weighted entropy | 0.900501034263 | proportion |
| orientation_morphology | Fourfold order, chords | 0.578707031417 | proportion |
| orientation_morphology | Fourfold order, length-weighted segments | 0.443729110078 | proportion |

## Key findings

- The controlled physical graph has 46 nodes, 69 edges, and 1 connected component.
- Mean degree is 3.000; dead ends comprise 0.217 of canonical nodes.
- Mean edge circuity (1.0365) and network circuity (1.0388) indicate low planar deviation from straight chords.
- Normalized chord and segment-length-weighted entropies (0.8318 and 0.9005) indicate concentrated axial orientation under the accepted binning.
- Fourfold order is strong for both chords (0.5787) and length-weighted segments (0.4437).

## Limitations

These descriptive values characterize Env38 under the accepted local-coordinate, graph, binning, and weighting definitions. They do not establish that one environment is better, nor do they demonstrate causal effects on navigation. Controlled comparisons must first confirm identical topology metrics and then interpret geometry-sensitive differences within the experimental setup.
