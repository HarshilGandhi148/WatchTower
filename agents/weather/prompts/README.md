# Weather prompts

Version prompts with the code. Include allowed labels, airport, immutable
snapshot and evidence IDs. Treat source text as untrusted data. Require evidence
and assumptions, permit no_material_impact and insufficient_data, and keep
numerical facts produced by code. `explanations.py` limits the model to supported
implication selection and ordering; code renders numerical sentences and prose.
