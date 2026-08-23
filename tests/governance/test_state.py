from __future__ import annotations

import pytest

from github_governance.__main__ import main
from github_governance.errors import GovernanceError
from github_governance.state import TRANSITIONS, can_transition, require_transition


def test_every_declared_transition_is_accepted():
    for entity, states in TRANSITIONS.items():
        for before, targets in states.items():
            for after in targets:
                assert can_transition(entity, before, after)
                require_transition(entity, before, after)


def test_all_other_known_state_pairs_are_rejected():
    for entity, states in TRANSITIONS.items():
        for before in states:
            for after in states:
                if after not in states[before]:
                    assert not can_transition(entity, before, after)
                    with pytest.raises(GovernanceError) as captured:
                        require_transition(entity, before, after)
                    assert captured.value.finding.id == "STATE-TRANSITION"


def test_intake_and_candidate_never_transition_to_ready():
    assert all(not can_transition(entity, state, "ready") for entity in ("intake", "candidate") for state in TRANSITIONS[entity])


def test_promoted_candidate_can_only_begin_a_new_draft():
    assert TRANSITIONS["candidate"]["promoted"] == {"draft"}


@pytest.mark.parametrize(
    "arguments,finding",
    [
        (["--entity", "engineering", "--from-state", "contracted", "--to-state", "done"], "STATE-TRANSITION"),
        (["--entity", "engineering", "--from-state", "unknown", "--to-state", "ready"], "STATE-UNKNOWN"),
        (["--entity", "engineering", "--from-state", "contracted", "--to-state", "unknown"], "STATE-UNKNOWN"),
        (["--entity", "unknown", "--from-state", "draft", "--to-state", "ready"], "STATE-UNKNOWN"),
    ],
)
def test_transition_cli_conflicts_use_stable_exit_3(arguments, finding, capsys):
    assert main(["transition", *arguments]) == 3
    output = capsys.readouterr()
    assert output.out == ""
    assert f'"{finding}"' in output.err
