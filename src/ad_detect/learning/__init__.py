"""Dataset export and causal replay tools."""

from .aggregation import aggregate_feature_windows
from .dataset import DatasetManifest, export_dataset, import_dataset, load_dataset_manifest
from .embeddings import EmbeddingAdapter, EmbeddingBenchmark, benchmark_embedding_adapter
from .evaluation import EvaluationArtifacts, run_evaluation
from .examples import LabeledExample, SessionSplit, grouped_session_split, label_feature_windows
from .fingerprint_replay import FingerprintReplayEvaluator
from .ml_replay import MLReplayEvaluator
from .model import (
    CandidateMetrics,
    ModelAlgorithm,
    ModelBundleManifest,
    ModelModality,
    TrainedTabularModel,
    evaluate_candidate,
    load_model_bundle,
    save_model_bundle,
    train_candidate,
)
from .registry import (
    ACTIVATE_MODEL,
    REGISTER_MODEL,
    AuthorizedActor,
    ModelRegistry,
)
from .replay import ReplayMetrics, ReplayPrediction, ReplayResult, RulesReplayEvaluator
from .temporal_replay import TemporalComparisonEvaluator, TemporalComparisonResult

__all__ = [
    "DatasetManifest",
    "FingerprintReplayEvaluator",
    "LabeledExample",
    "MLReplayEvaluator",
    "CandidateMetrics",
    "EmbeddingAdapter",
    "EmbeddingBenchmark",
    "EvaluationArtifacts",
    "ModelAlgorithm",
    "ModelBundleManifest",
    "ModelModality",
    "ModelRegistry",
    "ReplayMetrics",
    "ReplayPrediction",
    "ReplayResult",
    "RulesReplayEvaluator",
    "AuthorizedActor",
    "ACTIVATE_MODEL",
    "REGISTER_MODEL",
    "TemporalComparisonEvaluator",
    "TemporalComparisonResult",
    "SessionSplit",
    "TrainedTabularModel",
    "aggregate_feature_windows",
    "benchmark_embedding_adapter",
    "export_dataset",
    "evaluate_candidate",
    "import_dataset",
    "grouped_session_split",
    "label_feature_windows",
    "load_dataset_manifest",
    "load_model_bundle",
    "run_evaluation",
    "save_model_bundle",
    "train_candidate",
]
