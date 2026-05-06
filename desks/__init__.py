"""Active desk registry.

Production desks (always live):
    - OvernightCondorsDesk       (Bot A — control)
    - AfternoonButterfliesDesk   (Desk 2 — 0DTE butterflies)

Phase 2 multi-bot parallel paper trial (per
~/.claude/plans/okay-buddy-now-slow-enchanted-dongarra.md):
    - AsymmetricCondorsDesk      (Bot B — asymmetric IC)
    - OvernightPutspreadDesk     (Bot C — put-spread only)
    - OvernightCondorsVvixDesk   (Bot D — VVIX-conditional sizing)
    - OvernightCondorsDowDesk    (Bot E — DOW multiplier)
    - OvernightCondorsMaxDesk    (Bot F — thesis-maximizing combined)

All paper-trial bots share the *same* signal engine as Bot A. They differ only
in:
    1. Webhook URL prefix (each routes to its own OA bot group)
    2. Structure tag for log attribution
    3. Optional signal-transform hook that rewrites the tier label before
       webhook fire (Bots D and E)

If webhook URLs for a given Phase 2 bot aren't configured (env keys missing),
the bot still loads but its webhooks no-op — useful while waiting for OA
recipes to be set up.
"""
from desks.overnight_condors.desk import OvernightCondorsDesk
from desks.afternoon_butterflies.desk import AfternoonButterfliesDesk

# Phase 2 paper-trial bots
from desks.asymmetric_condors.desk import AsymmetricCondorsDesk
from desks.overnight_putspread.desk import OvernightPutspreadDesk
from desks.overnight_condors_vvix.desk import OvernightCondorsVvixDesk
from desks.overnight_condors_dow.desk import OvernightCondorsDowDesk
from desks.overnight_condors_max.desk import OvernightCondorsMaxDesk

ACTIVE_DESKS = [
    # Production (live)
    OvernightCondorsDesk(),
    AfternoonButterfliesDesk(),
    # Phase 2 paper trial
    AsymmetricCondorsDesk(),
    OvernightPutspreadDesk(),
    OvernightCondorsVvixDesk(),
    OvernightCondorsDowDesk(),
    OvernightCondorsMaxDesk(),
]
