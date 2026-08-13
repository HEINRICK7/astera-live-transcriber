import re
import unicodedata

from .models import RiskClass, VocabularyTerm

HIGH_RISK_TOKENS = frozenset(
    {
        "não",
        "nao",
        "negativo",
        "positivo",
        "esquerdo",
        "direito",
        "idade",
        "dose",
        "mg",
        "ml",
        "mcg",
        "g",
        "kg",
        "cm",
        "mmhg",
        "positivo",
        "negativo",
        "usa",
        "hipertensao",
        "hipertensão",
        "hipotensao",
        "hipotensão",
        "diagnostico",
        "diagnóstico",
        "alergia",
        "medicamento",
    }
)


def classify_risk(
    observed: str,
    canonical: str,
    vocabulary_term: VocabularyTerm | None = None,
) -> RiskClass:
    if vocabulary_term is not None and vocabulary_term.risk_class is RiskClass.HIGH:
        return RiskClass.HIGH
    tokens = set(re.findall(r"[\wÀ-ÿ]+", f"{observed} {canonical}".lower()))
    if tokens & HIGH_RISK_TOKENS or re.search(r"\d", f"{observed} {canonical}"):
        return RiskClass.HIGH
    return RiskClass.LOW


def normalize_for_matching(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value.lower())
    without_accents = "".join(char for char in decomposed if not unicodedata.combining(char))
    return " ".join(without_accents.split())
