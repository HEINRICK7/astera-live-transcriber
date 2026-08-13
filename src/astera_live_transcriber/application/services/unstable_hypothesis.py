import re


class UnstableHypothesisReconciler:
    """Reconciles only the provider's still-unstable turn hypothesis.

    This class deliberately uses token spans and conservative partial-word
    compatibility. It does not use fuzzy matching or semantic equivalence.
    """

    _MIN_DUPLICATE_TOKENS = 2
    _MIN_OVERLAP_TOKENS = 2
    _MIN_PARTIAL_WORD_LENGTH = 3

    def collapse_repeated_blocks(self, text: str) -> str:
        tokens = text.split()
        normalized = self._normalized_tokens(text)
        maximum = len(normalized) // 2
        for count in range(maximum, self._MIN_DUPLICATE_TOKENS - 1, -1):
            for start in range(0, len(normalized) - count * 2 + 1):
                if normalized[start : start + count] != normalized[
                    start + count : start + count * 2
                ]:
                    continue
                return " ".join(tokens[: start + count] + tokens[start + count * 2 :]).strip()
        return text

    def reconcile(self, current: str, incoming: str) -> str:
        incoming = self.collapse_repeated_blocks(incoming.strip())
        current = current.strip()
        if not current:
            return incoming

        internal_revision = self._replace_middle_span(current, incoming)
        if internal_revision is not None:
            return internal_revision

        replayed = self._remove_replayed_suffix(current, incoming)
        if replayed is not None:
            return replayed
        if self._same_text(current, incoming):
            return current
        if incoming.startswith(current):
            return incoming
        if current.startswith(incoming):
            return current

        common_prefix = self._common_prefix_tokens(current, incoming)
        if common_prefix >= 1:
            # Same turn, revised hypothesis: replace the unstable version.
            return incoming

        overlap = self._suffix_prefix_overlap(current, incoming)
        if overlap >= self._MIN_OVERLAP_TOKENS:
            return self._join_tokens(current, incoming.split()[overlap:])

        # Some providers emit a delta after a short partial.
        return self._join_tokens(current, incoming.split())

    def _replace_middle_span(self, current: str, incoming: str) -> str | None:
        old_tokens = self._normalized_tokens(current)
        new_tokens = self._normalized_tokens(incoming)
        if not old_tokens or not new_tokens:
            return None

        prefix = 0
        for old_token, new_token in zip(old_tokens, new_tokens):
            if old_token != new_token:
                break
            prefix += 1

        if prefix == 0 or prefix == len(old_tokens) or prefix == len(new_tokens):
            return None

        suffix = 0
        while (
            suffix < len(old_tokens) - prefix
            and suffix < len(new_tokens) - prefix
            and old_tokens[-(suffix + 1)] == new_tokens[-(suffix + 1)]
        ):
            suffix += 1

        old_middle = old_tokens[prefix : len(old_tokens) - suffix or None]
        new_middle = new_tokens[prefix : len(new_tokens) - suffix or None]
        if old_middle == new_middle:
            return None
        if (
            new_middle == old_tokens[: len(new_middle)]
            or new_middle == old_tokens[-len(new_middle) :]
        ):
            # The apparent middle change is a replayed boundary span. Let
            # the replay reconciler remove it instead of treating it as a
            # legitimate insertion.
            return None
        return incoming

    def _remove_replayed_suffix(self, current: str, incoming: str) -> str | None:
        current_tokens = current.split()
        incoming_tokens = incoming.split()
        normalized_current = self._normalized_tokens(current)
        normalized_incoming = self._normalized_tokens(incoming)
        if not normalized_current or not normalized_incoming:
            return None
        incoming_starts_with_current = self._span_compatible(
            normalized_current,
            normalized_incoming[: len(normalized_current)],
        )

        # Find a later copy of a suffix of the current hypothesis inside the
        # incoming hypothesis. The later copy is the provider's more complete
        # hypothesis; discard the stale prefix and keep the latest span.
        for count in range(len(normalized_current), 0, -1):
            if count < self._MIN_OVERLAP_TOKENS and not incoming_starts_with_current:
                continue
            suffix = normalized_current[-count:]
            for start in range(1, len(normalized_incoming) - count + 1):
                baseline_start = len(normalized_current) - count
                if incoming_starts_with_current and start <= baseline_start:
                    continue
                aligned_prefix = self._span_compatible(
                    normalized_current[:baseline_start],
                    normalized_incoming[:start],
                )
                if start <= baseline_start and not aligned_prefix:
                    continue
                candidate = normalized_incoming[start : start + count]
                if not self._span_compatible(suffix, candidate):
                    continue
                prefix = current_tokens[: len(current_tokens) - count]
                return self._join_tokens(" ".join(prefix), incoming_tokens[start:])
        return None

    @classmethod
    def _span_compatible(cls, left: list[str], right: list[str]) -> bool:
        return len(left) == len(right) and all(
            cls._tokens_compatible(left_token, right_token)
            for left_token, right_token in zip(left, right)
        )

    def _common_prefix_tokens(self, left: str, right: str) -> int:
        left_tokens = self._normalized_tokens(left)
        right_tokens = self._normalized_tokens(right)
        count = 0
        for left_token, right_token in zip(left_tokens, right_tokens):
            if left_token != right_token:
                break
            count += 1
        return count

    def _suffix_prefix_overlap(self, current: str, incoming: str) -> int:
        current_tokens = self._normalized_tokens(current)
        incoming_tokens = self._normalized_tokens(incoming)
        maximum = min(len(current_tokens), len(incoming_tokens))
        for count in range(maximum, self._MIN_OVERLAP_TOKENS - 1, -1):
            if current_tokens[-count:] == incoming_tokens[:count]:
                return count
        return 0

    @classmethod
    def _tokens_compatible(cls, left: str, right: str) -> bool:
        if left == right:
            return True
        shorter, longer = sorted((left, right), key=len)
        return (
            len(shorter) >= cls._MIN_PARTIAL_WORD_LENGTH
            and longer.startswith(shorter)
        )

    @staticmethod
    def _normalized_tokens(text: str) -> list[str]:
        return [
            re.sub(r"[^\wÀ-ÿ]+", "", token.casefold())
            for token in text.split()
            if re.sub(r"[^\wÀ-ÿ]+", "", token.casefold())
        ]

    @staticmethod
    def _same_text(left: str, right: str) -> bool:
        def normalize(value: str) -> str:
            return re.sub(r"\s+", " ", value).strip().casefold()

        return normalize(left) == normalize(right)

    @staticmethod
    def _join_tokens(prefix: str, suffix: list[str]) -> str:
        return " ".join([prefix.strip(), *suffix]).strip()
