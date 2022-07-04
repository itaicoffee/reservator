import csv
import json
from dataclasses import dataclass
from datetime import datetime as dt, date, timedelta
from io import StringIO
from time import sleep
from typing import Generator, Tuple, Optional

import requests
from flask import Flask, request, render_template
from requests import HTTPError

_VENUE_HITS = []

_ROOT = "https://api.resy.com/2"
_ROOT_3 = "https://api.resy.com/3"
_ROOT_4 = "https://api.resy.com/4"

# Authentication token. Will be set later.
_TOKEN = ""


def get_headers() -> dict[str, str]:
    return {
        "origin": "https://resy.com",
        "accept-encoding": "gzip, deflate, br",
        "x-origin": "https://resy.com",
        "accept-language": "en-US,en;q=0.9",
        "authorization": 'ResyAPI api_key="VbWk7s3L4KiK5fzlO7JD3Q5EYolJI7n5"',
        "content-type": "application/x-www-form-urlencoded",
        "accept": "application/json, text/plain, */*",
        "referer": "https://resy.com/",
        "authority": "api.resy.com",
        "user-agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.114 Safari/537.36",
        "x-resy-auth-token": _TOKEN,
        "x-resy-universal-auth": _TOKEN,
    }


_VENUE = {
    "dante": "1290",
    "carbone": "6194",
}

_TIME = {
    "evening": ("21:15:00", "21:45:00"),
}

_DAY_ORDINAL = dict(
    (y, x)
    for x, y in enumerate(
        [
            "monday",
            "tuesday",
            "wednesday",
            "thursday",
            "friday",
            "saturday",
            "sunday",
        ]
    )
)

_DATE_FORMAT = "%Y-%m-%d"


@dataclass
class ReservationItem:
    day: str
    time: str
    num_seats: int
    venue_id: str
    venue_name: str
    kind: str = "Outdoors"
    token: str = ""


@dataclass
class AskItem:
    start_day: str
    end_day: str
    start_time: str
    end_time: str
    num_seats: int
    venue_names: list[str]


def _date_to_day(date_: dt.date) -> str:
    return date_.strftime(_DATE_FORMAT)


def _day_to_date(day: str) -> dt:
    return dt.strptime(day, _DATE_FORMAT)


def _next_day(day_ordinal: int) -> dt.date:
    today = date.today()
    return today + timedelta((day_ordinal - today.weekday()) % 7)


def _dates_between(start_date: dt, end_date: dt):
    num_dates = (end_date - start_date).days
    for i in range(num_dates + 1):
        yield start_date + timedelta(days=i)


def _days_between(start_day: str, end_day: str):
    start_date = _day_to_date(start_day)
    end_date = _day_to_date(end_day)
    for date_ in _dates_between(start_date, end_date):
        yield _date_to_day(date_)


def _get_venue_name(venue_id: str) -> Optional[str]:
    for venue_name, venue_id_ in _VENUE.items():
        if venue_id_ == venue_id:
            return venue_name
    return None


def _load_values(url: str):
    r = requests.get(url)
    r.raise_for_status()
    f = StringIO(r.text)
    return csv.reader(f, delimiter="\t")


def _load_auth_tokens() -> Generator[Tuple[str, str, str], None, None]:
    url = "https://docs.google.com/spreadsheets/d/1CCj6cAab3Ftw-nOrIOJQtOhkCISkXMa_Xgr8WHgPxdw/export?format=tsv"
    reader = _load_values(url)
    for row in reader:
        name, auth_token, ask_url = row
        yield name, auth_token, ask_url


def _split_as_range(s: str) -> list[str]:
    result = s.split(" - ")
    if len(result) == 2:
        return result
    if len(result) == 1:
        return [result[0], result[0]]
    assert False


def _load_asks(url: str) -> Generator[AskItem, None, None]:
    reader = _load_values(url)
    for row in reader:
        try:
            day_range, time_range, num_seats, venue_names = row
        except ValueError:
            print(f"failed to parse row {row}")
            continue
        start_day, end_day = _split_as_range(day_range)
        start_time, end_time = _split_as_range(time_range)
        num_seats = int(num_seats)
        venue_names = [s.strip() for s in venue_names.split(",") if len(s) > 0]
        yield AskItem(
            start_day,
            end_day,
            start_time,
            end_time,
            num_seats,
            venue_names,
        )


def _load_venues(venue_names: list[str]):
    for venue_name in venue_names:
        if venue_name in _VENUE:
            continue
        venue_id = get_venue_id_by_search(venue_name)
        if venue_id is not None:
            _VENUE[venue_name] = venue_id
            print(f"fetched {venue_name}: {venue_id}")
        else:
            print(f"failed to fetch {venue_name}")


def _load_venue_hits():
    _load_venues(_VENUE_HITS)


def _get_availability(
    venue_id: str, day: str, num_seats: int, start_time: str, end_time: str
):
    url = (
        _ROOT_4
        + f"/find?lat=0&long=0&day={day}&party_size={num_seats}&venue_id={venue_id}"
        f"&time_preferred_start={start_time}?&time_preferred_end={end_time}"
    )
    r = requests.get(url, headers=get_headers())
    r.raise_for_status()
    return r.json()


def _get_book_token(token: str):
    url = _ROOT_3 + "/details"
    data = {
        "commit": 1,
        "config_id": token,
        "day": "2022-05-10",
        "party_size": 2,
    }
    _headers = get_headers()
    _headers["content-type"] = "application/json;charset=UTF-8"
    r = requests.post(url, data=json.dumps(data), headers=_headers)
    r.raise_for_status()
    return r.json()


def get_book_token(token: str) -> str:
    data = _get_book_token(token)
    return data["book_token"]["value"]


def _reserve(book_token: str, force_replace: bool):
    url = _ROOT_3 + "/book"
    data = {
        "book_token": book_token,
        "struct_payment_method": '{"id":9293374}',
        "source_id": "resy.com-venue-details",
    }
    if force_replace:
        data["replace"] = "1"
    r = requests.post(url, data=data, headers=get_headers())
    try:
        r.raise_for_status()
    except HTTPError as e:
        print(f"failed to book: {e}")
        return None
    return r.json()


def reserve(book_token: str) -> Optional[Tuple[str, str]]:
    data = _reserve(book_token=book_token, force_replace=True)
    if data is None:
        return None
    resy_token = data["resy_token"]
    reservation_id = data["reservation_id"]
    return resy_token, reservation_id


def _get_reservations():
    url = _ROOT_3 + f"/user/reservations"
    params = {
        "limit": 10,
        "offset": 1,
        "type": "upcoming",
        "book_on_behalf_of": False,
    }
    r = requests.get(url, params=params, headers=get_headers())
    try:
        r.raise_for_status()
    except HTTPError:
        return None
    return r.json()


def get_reservations():
    data = _get_reservations()
    if data is None:
        print("failed to fetch current reservations")
        return
    reservations = data.get("reservations")
    if reservations is None or len(reservations) == 0:
        return
    for res in reservations:
        venue_id_int = res["venue"]["id"]
        venue_id_str = str(venue_id_int)
        venue = data["venues"].get(venue_id_str)
        if venue is None:
            print(f"failed to find venue {res['venue']['id']}")
            continue
        day = res["day"]
        time = res["time_slot"]
        num_seats = res["num_seats"]
        venue_id = venue_id_str
        venue_name = venue["name"]
        item = ReservationItem(
            day=day,
            time=time,
            num_seats=num_seats,
            venue_id=venue_id,
            venue_name=venue_name,
        )
        yield item


def get_availability(
    venue_id: str, day: str, num_seats: int, start_time: str, end_time: str
):
    print(
        f"checking availability: {_get_venue_name(venue_id) or venue_id}, {day}, {num_seats}"
    )
    r = _get_availability(venue_id, day, num_seats, start_time, end_time)
    if len(r["results"]["venues"]) == 0:
        print(f"failed to get availability: {_get_venue_name(venue_id) or venue_id}")
        return
    data = r["results"]["venues"][0]
    slots = data["slots"]
    for slot in slots:
        start_time = slot["date"]["start"].split(" ")[1]
        kind = slot["config"]["type"].lower()
        token = slot["config"]["token"]
        venue_name = data["venue"]["name"]
        # 2022-05-09 19:15:00 (dining room)
        yield ReservationItem(
            day=day,
            time=start_time,
            num_seats=num_seats,
            venue_id=str(venue_id),
            venue_name=venue_name,
            kind=kind,
            token=token,
        )


def get_hit_list_availability(days: list[str], num_seats: int) -> str:
    start_time = "19:00:00"
    end_time = "21:00:00"
    s = []
    for venue_name, venue_id in _VENUE.items():
        for day in days:
            slots = get_availability(venue_id, day, num_seats, start_time, end_time)
            slots = [slot for slot in slots if "19:00:00" <= slot.time < "21:00:00"]
            if len(slots) > 0:
                s.append(venue_name)
                for slot in slots:
                    s.append(str(slot))
    return "\n".join(s)


def post_notify_route(
    venue_id: str, day: str, start_time: str, end_time: str, num_seats: int
):
    url = _ROOT + "/notify"
    data = {
        "venue_id": venue_id,
        "day": day,
        "time_preferred_start": start_time,
        "time_preferred_end": end_time,
        "num_seats": num_seats,
        "service_type_id": 2,
    }
    r = requests.post(url, headers=get_headers(), data=data)
    r.raise_for_status()
    return r


def notify(venue_name: str, day_name: str, time_name: str, num_seats):
    venue_id = _VENUE[venue_name]
    if day_name in _DAY_ORDINAL:
        day = _date_to_day(_next_day(_DAY_ORDINAL[day_name]))
    else:
        day = day_name
    start_time, end_time = _TIME[time_name]
    return post_notify_route(
        venue_id=venue_id,
        day=day,
        start_time=start_time,
        end_time=end_time,
        num_seats=num_seats,
    )


def get_search_route(query, day, num_seats):
    url = _ROOT_3 + "/venuesearch/search"
    data = {
        "geo": {"latitude": 40.7157, "longitude": -74},
        "highlight": {"pre_tag": "<b>", "post_tag": "</b>"},
        "per_page": 10,
        "query": query,
        "slot_filter": {"day": day, "party_size": num_seats},
        "types": ["venue", "cuisine"],
    }
    _headers = get_headers()
    _headers["content-type"] = "application/json;charset=UTF-8"
    r = requests.post(url, headers=_headers, data=json.dumps(data))
    r.raise_for_status()
    return r


def get_venue_id_by_search(query) -> Optional[str]:
    r = get_search_route(query, _date_to_day(date.today()), 2)
    try:
        data = r.json()
        hits = data["search"]["hits"]
        if len(hits) == 0:
            return None
        id_ = hits[0]["id"]["resy"]
    except Exception as e:
        print(f"failed to fetch venue by search {query} {e}")
        return None
    return str(id_)


def schedule_notifications(num_seats: int, next_x_days: int):
    today = date.today()
    _load_venue_hits()
    for venue_name in _VENUE.keys():
        for i in (2,):
            day_name = _date_to_day(today + timedelta(i))
            notify(
                venue_name=venue_name,
                day_name=day_name,
                time_name="evening",
                num_seats=num_seats,
            )
            print(f"scheduled {venue_name} on {day_name} for {num_seats}")
            sleep(0.5)


def _is_valid_ask(ask: AskItem) -> bool:
    return ask.start_time <= ask.end_time and ask.start_day <= ask.end_day


def _is_future_ask(ask: AskItem) -> bool:
    return ask.end_day >= _date_to_day(date.today())


def is_valid_ask(ask: AskItem) -> bool:
    if not _is_valid_ask(ask):
        print(f"? encountered invalid ask {ask}")
        return False
    return _is_future_ask(ask)


def process_ask(ask: AskItem):
    # check that there isn't a reservation at this time
    reservations = get_reservations()
    if reservations is None:
        return
    reservations = list(reservations)
    print(f"checking {len(reservations)} reservations")
    reservations_venue_ids = {
        res.venue_id
        for res in reservations
        if (
            ask.start_day <= res.day <= ask.end_day
            and ask.start_time <= res.time <= ask.end_time
            # don't check num seats...
        )
    }
    if len(reservations_venue_ids) > 0:
        print(f"already has {len(reservations_venue_ids)} conflicting reservations")

    # load venues
    _load_venues(ask.venue_names)

    for venue_name in ask.venue_names:
        venue_id = _VENUE.get(venue_name)
        if venue_id is None:
            continue
        if venue_id in reservations_venue_ids:
            print(
                f"best reservation already booked at {_get_venue_name(venue_id) or venue_id}"
            )
            return
        print(f"searching venue {venue_name}")
        for day in _days_between(ask.start_day, ask.end_day):
            slots = get_availability(
                venue_id, day, ask.num_seats, ask.start_time, ask.end_time
            )
            for slot in slots:
                if slot.time < ask.start_time or slot.time > ask.end_time:
                    continue
                if any(
                    [s in slot.kind.lower() for s in ("outdoors", "outdoor", "patio")]
                ):
                    print(f"skipping [{slot.kind.lower()} slot {slot}")
                    continue
                print(f"getting book token for {slot}")
                book_token = get_book_token(slot.token)
                print(f"reserving using book token {book_token}")
                reservation_result = reserve(book_token)
                if reservation_result is None:
                    print(f"failed to get book {slot}")
                else:
                    resy_token, reservation_id = reservation_result
                    print(
                        f"reserved with resy token {resy_token} and reservation id {reservation_id}"
                    )
                return
        print(f"no slots found for venue {venue_name}")

    print(f"no slots found for {len(ask.venue_names)} venues")


app = Flask(__name__)


@app.route("/", methods=("GET", "POST"))
def hello_world():
    if request.method == "POST":
        start_date = request.form["start_date"]
        end_date = request.form.get("end_date")
        if end_date is None or len(end_date) == 0:
            end_date = start_date
        num_seats = request.form["num_seats"]
        venues = request.form["venues"].split("\n")

        format_ = "%Y-%m-%d"
        start_date = dt.strptime(start_date, format_)
        end_date = dt.strptime(end_date, format_)
        num_dates = (end_date - start_date).days
        days = (start_date + timedelta(days=x) for x in range(num_dates + 1))
        days = [d.strftime(format_) for d in days]

        global _VENUE
        _VENUE = {}
        for venue in venues:
            venue_id = get_venue_id_by_search(venue)
            if venue_id is not None:
                _VENUE[venue] = venue_id
        result = get_hit_list_availability(days, num_seats)
        return "<br>".join(result.split("\n"))
        # if not title:
        #     flash('Title is required!')
        # elif not content:
        #     flash('Content is required!')
        # else:
        #     messages.append({'title': title, 'content': content})
        #     return redirect(url_for('index'))
    # return "Hi there"
    if True:
        return render_template("main.html")
    _load_venue_hits()
    return f"Hello from Flask! loaded {len(_VENUE)} hits."


def main():
    if True:
        for name, auth_token, ask_url in _load_auth_tokens():
            global _TOKEN
            _TOKEN = auth_token
            print(f"loading asks for: {name}")
            asks = list(_load_asks(ask_url))
            num_asks_skipped = 0
            for ask in asks:
                if not is_valid_ask(ask):
                    num_asks_skipped += 1
                    continue
                process_ask(ask)
            print(f"skipped {num_asks_skipped} rows")
        return
    # num_seats_options = (4,)  # (2, 4, 6)
    # next_x_days = 10
    # for num_seats in num_seats_options:
    #     schedule_notifications(num_seats, next_x_days)


if __name__ == "__main__":
    main()
