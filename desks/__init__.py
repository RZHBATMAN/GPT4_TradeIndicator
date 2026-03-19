"""Active desk registry."""
from desks.overnight_condors.desk import OvernightCondorsDesk
from desks.afternoon_butterflies.desk import AfternoonButterfliesDesk

ACTIVE_DESKS = [OvernightCondorsDesk(), AfternoonButterfliesDesk()]
