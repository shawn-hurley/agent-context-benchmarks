"""Utility functions for ACB."""

from __future__ import annotations


def normalize_instance_id_for_path(instance_id: str) -> str:
    """Convert instance_id to filesystem-safe directory name.
    
    Replaces slashes with double underscores to flatten hierarchical IDs.
    This ensures all instance directories are created at the same level,
    regardless of whether the instance_id contains path separators.
    
    Examples:
        "psf__requests-1142" → "psf__requests-1142" (unchanged)
        "business_domain/cart/jakarta-to-quarkus" → "business_domain__cart__jakarta-to-quarkus"
    
    Args:
        instance_id: The instance identifier, potentially with slashes
        
    Returns:
        A filesystem-safe directory name with slashes replaced by double underscores
    """
    return instance_id.replace("/", "__")
