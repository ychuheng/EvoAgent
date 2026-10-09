"""One accounting definition for service, worker reservations and runtime gates."""

from sqlalchemy import and_, case, or_

from evoagent.db.models import LearningSpendReservationRecord as Reservation


def unknown_usage():
    return or_(
        Reservation.status == "unknown",
        and_(
            Reservation.status == "settled",
            or_(Reservation.actual_micros.is_(None), Reservation.actual_micros < 0),
        ),
    )


def unresolved_usage():
    return or_(Reservation.status == "reserved", unknown_usage())


def usage_upper_bound():
    return case(
        (Reservation.status == "released", 0),
        (
            (Reservation.status == "settled") & (Reservation.actual_micros >= 0),
            Reservation.actual_micros,
        ),
        else_=Reservation.reserved_micros,
    )
