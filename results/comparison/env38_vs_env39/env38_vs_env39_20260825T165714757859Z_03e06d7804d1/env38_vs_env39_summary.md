# Controlled Env38 versus Env39 street-morphology comparison

## Research question

Given equal canonical graph topology, how do the accepted curvilinear Env38 and grid-like Env39 realizations differ in geometry-weighted network structure, planar geometry, and axial orientation morphology?

## Experimental control

Both networks contain 46 nodes, 69 physical edges, one component, mean degree 3.0, 10 dead ends, 16 degree-3 nodes, 20 degree-4 nodes, and cycle rank 24. Their topology signature is `4AF93910532EB430103267DAECF73B3ADC3B3D1599471EDF6B1632B6C5F898CB`. This confirms connectivity control, not morphological identity.

## Data provenance

- Env39 accepted run: `env39_20260825T081117893828Z_2046798c1e9c`
- Env38 accepted run: `env38_20260825T162856878635Z_974f687c1ac4`
- Env38 Phase 9F reproducibility: 39/39 comparison metrics exact, 19/19 core metrics exact, zero scientific mismatches.

## Geometry-weighted network comparison

Mean length-weighted shortest path is 724.2999737149149 local units in Env39 and 721.4252938680377 local units in Env38. Hop-based topology remains controlled; the weighted difference reflects changed local geometric edge costs.

## Geometric morphology

Total physical-network length is 10945.657681153116 local units in Env39 and 11398.854303944323 in Env38. Network circuity is 1.022596526286689 and 1.038846945394489, respectively. LineString length follows the stored path, chord length joins endpoints, edge circuity is their edge-level ratio, and network circuity is the ratio of aggregate lengths.

## Orientation morphology

Normalized chord entropy changes from 0.22976612828040105 (Env39) to 0.8318095232640588 (Env38), while chord phi changes from 0.9511069268378259 to 0.5787070314167302. The same axial [0,180), north-zero/east-90 clockwise convention, 36 five-degree bins, natural-log entropy, and fourfold order definition are used.

## Key descriptive findings

- `orientation.segment_entropy_length_weighted`: Env39 0.6896030136902007; Env38 3.2269625103808837; signed change 2.5373594966906827; relative change 367.945%.
- `orientation.segment_entropy_length_weighted_normalized`: Env39 0.19243738502113314; Env38 0.9005010342630304; signed change 0.7080636492418972; relative change 367.945%.
- `orientation.chord_entropy`: Env39 0.8233712721085531; Env38 2.9808051798049027; signed change 2.1574339076963494; relative change 262.024%.
- `orientation.chord_entropy_normalized`: Env39 0.22976612828040105; Env38 0.8318095232640588; signed change 0.6020433949836577; relative change 262.024%.
- `orientation.phi_segment_length_weighted`: Env39 1.0; Env38 0.4437291100780897; signed change -0.5562708899219103; relative change -55.627%.
- `orientation.phi_chord`: Env39 0.9511069268378259; Env38 0.5787070314167302; signed change -0.37239989542109575; relative change -39.154%.
- `geometry.maximum_edge_length`: Env39 674.9952524104815; Env38 505.5201047999172; signed change -169.4751476105643; relative change -25.108%.
- `geometry.median_edge_length`: Env39 131.0174803014961; Env38 143.4478602096824; signed change 12.430379908186296; relative change 9.488%.

## Methodological limitations

This is a controlled descriptive comparison of one accepted spatial realization per design. The second Env38 run is a computational reproducibility replicate, not an independent spatial sample. No p-values, hypothesis tests, confidence intervals, or causal/effect-significance claims are supported.

## Interpretation constraints

GeoGami Local Cartesian coordinates are not longitude/latitude. Distances remain local units and are not metres. Greater entropy, order, circuity, or weighted distance is not inherently better or worse, and behavioral or navigation conclusions require independent wayfinding evidence.

## Machine-readable outputs

- `results/comparison/env38_vs_env39/env38_vs_env39_20260825T165714757859Z_03e06d7804d1/comparison_metrics.csv`
- `results/comparison/env38_vs_env39/env38_vs_env39_20260825T165714757859Z_03e06d7804d1/comparison_core_metrics.csv`
- `results/comparison/env38_vs_env39/env38_vs_env39_20260825T165714757859Z_03e06d7804d1/comparison_summary.json`
- `results/comparison/env38_vs_env39/env38_vs_env39_20260825T165714757859Z_03e06d7804d1/comparison_methodology.json`
- `results/comparison/env38_vs_env39/env38_vs_env39_20260825T165714757859Z_03e06d7804d1/comparison_manifest.json`
