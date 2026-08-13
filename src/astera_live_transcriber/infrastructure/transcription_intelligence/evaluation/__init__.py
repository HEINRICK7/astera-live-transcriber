"""Offline evaluation adapters."""

from .benchmark import BenchmarkItem, JiwerBenchmark, JiwerBenchmarkReport
from .clinical_checks import (
    CLINICAL_CATEGORIES,
    ClinicalCheckEvaluator,
    ClinicalCheckItem,
    ClinicalCheckReport,
)
from .clinical_fidelity import ClinicalFidelityEvaluator, ClinicalFidelityReport
from .clinical_sampling import build_sample_plan
from .error_analysis import AttributedError, ErrorAnalysisReport, analyze_error_attribution
from .jiwer_evaluator import (
    AlignmentError,
    JiwerAlignment,
    JiwerEvaluator,
    JiwerScores,
    normalize_layout,
)
from .long_session import analyze_long_session
from .quality_attribution import analyze_quality_attribution

__all__ = [
    "BenchmarkItem",
    "CLINICAL_CATEGORIES",
    "ClinicalCheckEvaluator",
    "ClinicalCheckItem",
    "ClinicalCheckReport",
    "JiwerBenchmark",
    "JiwerBenchmarkReport",
    "JiwerEvaluator",
    "JiwerScores",
    "AlignmentError",
    "JiwerAlignment",
    "normalize_layout",
    "AttributedError",
    "ErrorAnalysisReport",
    "analyze_error_attribution",
    "ClinicalFidelityEvaluator",
    "ClinicalFidelityReport",
    "analyze_long_session",
    "analyze_quality_attribution",
    "build_sample_plan",
]
