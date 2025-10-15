"""
Weight Failure Classifier - Centralized error classification for weight setting failures

This module provides classification logic for weight setting errors to determine
their severity and appropriate handling.
"""


class WeightFailureClassifier:
    """Classifies weight setting failures into benign, critical, or unknown categories"""

    @staticmethod
    def classify_failure(error_msg: str) -> str:
        """
        Classify a weight setting failure based on the error message

        Args:
            error_msg: The error message from the failed weight setting operation

        Returns:
            str: Classification type - "benign", "critical", or "unknown"
        """
        error_lower = error_msg.lower()

        # BENIGN - Don't alert (expected behavior)
        if any(phrase in error_lower for phrase in [
            "no attempt made. perhaps it is too soon to commit weights",
            "too soon to commit weights",
            "too soon to commit"
        ]):
            return "benign"

        # CRITICAL - Alert immediately (known problematic patterns)
        elif any(phrase in error_lower for phrase in [
            "maximum recursion depth exceeded",
            "invalid transaction",
            "subtensor returned: invalid transaction"
        ]):
            return "critical"

        # UNKNOWN - Alert after pattern emerges
        else:
            return "unknown"

    @staticmethod
    def is_benign(error_msg: str) -> bool:
        """
        Check if an error is benign (should not trigger network switching or alerts)

        Args:
            error_msg: The error message from the failed operation

        Returns:
            bool: True if the error is benign, False otherwise
        """
        return WeightFailureClassifier.classify_failure(error_msg) == "benign"

    @staticmethod
    def is_critical(error_msg: str) -> bool:
        """
        Check if an error is critical (requires immediate attention)

        Args:
            error_msg: The error message from the failed operation

        Returns:
            bool: True if the error is critical, False otherwise
        """
        return WeightFailureClassifier.classify_failure(error_msg) == "critical"
