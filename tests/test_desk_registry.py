"""Tests for desk registry and desk base class.

Verifies all desks load, have required attributes, and can register routes.

Run: python -m pytest tests/test_desk_registry.py -v
"""
import pytest
from desks import ACTIVE_DESKS
from core.desk import Desk


class TestDeskRegistry:
    """Test that all desks are properly registered."""

    def test_desks_loaded(self):
        """At least one desk is registered."""
        assert len(ACTIVE_DESKS) >= 1

    def test_both_desks_present(self):
        """Both overnight condors and afternoon butterflies are registered."""
        desk_ids = [d.desk_id for d in ACTIVE_DESKS]
        assert 'overnight_condors' in desk_ids
        assert 'afternoon_butterflies' in desk_ids

    def test_unique_desk_ids(self):
        """All desk IDs are unique."""
        desk_ids = [d.desk_id for d in ACTIVE_DESKS]
        assert len(desk_ids) == len(set(desk_ids))

    def test_all_desks_have_required_attrs(self):
        """Every desk has the required attributes."""
        for desk in ACTIVE_DESKS:
            assert desk.desk_id, f"Missing desk_id on {desk}"
            assert desk.display_name, f"Missing display_name on {desk.desk_id}"
            assert desk.description, f"Missing description on {desk.desk_id}"
            assert desk.window_start is not None
            assert desk.window_end is not None
            assert isinstance(desk.window_days, list)

    def test_desks_are_desk_instances(self):
        """All desks inherit from Desk base class."""
        for desk in ACTIVE_DESKS:
            assert isinstance(desk, Desk)

    def test_desk_health(self):
        """get_health returns expected keys."""
        for desk in ACTIVE_DESKS:
            health = desk.get_health()
            assert 'desk_id' in health
            assert 'display_name' in health
            assert health['desk_id'] == desk.desk_id

    def test_desk_dashboard_html(self):
        """Each desk returns non-empty dashboard HTML."""
        for desk in ACTIVE_DESKS:
            html = desk.get_dashboard_html()
            assert isinstance(html, str)
            assert len(html) > 50

    def test_register_routes(self):
        """Routes can be registered on a Flask app."""
        from flask import Flask
        app = Flask(__name__)
        for desk in ACTIVE_DESKS:
            desk.register_routes(app)

        # Check expected routes exist
        rules = [r.rule for r in app.url_map.iter_rules()]
        assert '/overnight/trigger' in rules
        assert '/butterflies/trigger' in rules
