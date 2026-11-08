# Model artifacts

Generated model bundles are local runtime artifacts and are ignored by source
control. Phase 5 bundles are content-addressed directories containing a
`manifest.json` and `model.joblib`. The manifest binds the artifact hash,
feature and aggregation schemas, ordered columns and classes, source dataset
digest, modality, algorithm, calibration method, metrics, and exact
scikit-learn and NumPy versions.

The SHA-256 checks detect accidental or unauthorized modification; they do not
make an untrusted joblib file safe. Load only bundles generated locally from a
reviewed dataset. Training creates inactive candidates and never changes the
configured live model. Phase 7 registration verifies that the bundle appears
in a completed immutable evaluation and leaves it inactive. Activation and
rollback reload the bundle, require an authorized actor contract and reason,
update one transactional active pointer, and append an audit result. No
unauthenticated activation endpoint is provided.

