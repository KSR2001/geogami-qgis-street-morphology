# Env39 baseline summary

This machine-produced baseline integrates the accepted Phase 7F topology and Phase 7G geometry/orientation results; it does not recalculate metrics.

## Canonical identity

- Canonical run: `env39_20260825T081117893828Z_2046798c1e9c`
- Canonical SHA-256: `212A2AA583BF77304859DB76639B8AD4B6DC458237EEA24FEB60FC228E5D6847`
- Scientific-content signature: `2046798C1E9CB35310ABB4EA7134EE212423265572C278075A94ED0E9080D447`
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
| geometry_weighted_network | Mean weighted shortest-path length | 724.299973715 | local units |
| geometric_morphology | Total physical network length | 10945.6576812 | local units |
| geometric_morphology | Mean physical-edge length | 158.632720017 | local units |
| geometric_morphology | Mean edge circuity | 1.01114494972 | ratio |
| geometric_morphology | Network circuity | 1.02259652629 | ratio |
| orientation_morphology | Chord orientation entropy | 0.823371272109 | nats |
| orientation_morphology | Normalized chord orientation entropy | 0.22976612828 | proportion |
| orientation_morphology | Segment-length-weighted orientation entropy | 0.68960301369 | nats |
| orientation_morphology | Normalized segment-length-weighted entropy | 0.192437385021 | proportion |
| orientation_morphology | Fourfold order, chords | 0.951106926838 | proportion |
| orientation_morphology | Fourfold order, length-weighted segments | 1 | proportion |

## Key findings

- The controlled physical graph has 46 nodes, 69 edges, and 1 connected component.
- Mean degree is 3.000; dead ends comprise 0.217 of canonical nodes.
- Mean edge circuity (1.0111) and network circuity (1.0226) indicate low planar deviation from straight chords.
- Normalized chord and segment-length-weighted entropies (0.2298 and 0.1924) indicate concentrated axial orientation under the accepted binning.
- Fourfold order is strong for both chords (0.9511) and length-weighted segments (1.0000).

## Limitations

These descriptive values characterize Env39 under the accepted local-coordinate, graph, binning, and weighting definitions. They do not establish that one environment is better, nor do they demonstrate causal effects on navigation. A later Env38 comparison must first confirm identical topology-control metrics and then interpret geometry-sensitive differences within the experimental setup.
