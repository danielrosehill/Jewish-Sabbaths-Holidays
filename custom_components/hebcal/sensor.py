"""
Platform to get Hebcalh Times And Hebcalh information for Home Assistant.

Document will come soon...
"""
import logging
import json
import codecs
import pathlib
import datetime
import time
import aiohttp
import voluptuous as vol
from homeassistant.components.sensor import PLATFORM_SCHEMA
import homeassistant.helpers.config_validation as cv
from homeassistant.const import (
    CONF_LATITUDE, CONF_LONGITUDE, CONF_RESOURCES)
from homeassistant.helpers.entity import Entity
from homeassistant.core import callback
from homeassistant.helpers.entity import async_generate_entity_id
from homeassistant.components.sensor import ENTITY_ID_FORMAT

_LOGGER = logging.getLogger(__name__)

SENSOR_PREFIX = 'Hebcal '
HAVDALAH_MINUTES = 'havdalah_calc'
TIME_BEFORE_CHECK = 'time_before_check'
TIME_AFTER_CHECK = 'time_after_check'

SENSOR_TYPES = {
    'shabbat_in': ['כניסת השבת', 'mdi:candle', 'shabbat_in'],
    'shabbat_out': ['צאת השבת', 'mdi:exit-to-app', 'shabbat_out'],
    'is_shabbat': ['האם שבת', 'mdi:candle', 'is_shabbat'],
    'parasha': ['פרשת השבוע', 'mdi:book-open-variant', 'parasha'],
    'holiday_in': ['כניסת החג', 'mdi:candle', 'holiday_in'],
    'holiday_out': ['צאת החג', 'mdi:exit-to-app', 'holiday_out'],
    'is_holiday': ['האם חג', 'mdi:candle', 'is_holiday'],
    'holiday_name': ['שם החג', 'mdi:book-open-variant', 'holiday_name'],
    'hebrew_date': ['תאריך עברי', 'mdi:calendar', 'hebrew_date'],
}

PLATFORM_SCHEMA = PLATFORM_SCHEMA.extend({
    vol.Optional(CONF_LATITUDE): cv.latitude,
    vol.Optional(CONF_LONGITUDE): cv.longitude,
    vol.Optional(HAVDALAH_MINUTES, default=42): int,
    vol.Optional(TIME_BEFORE_CHECK, default=10): int,
    vol.Optional(TIME_AFTER_CHECK, default=10): int,
    vol.Optional(CONF_RESOURCES, default=['shabbat_in', 'shabbat_out', 'parasha', 'hebrew_date', 'is_shabbat',
                                          'holiday_in', 'holiday_out', 'is_holiday', 'holiday_name']):
        vol.All(cv.ensure_list, [vol.In(SENSOR_TYPES)]),
})


async def async_setup_platform(
        hass, config, async_add_entities, discovery_info=None):
    """Set up the Hebcal config sensors."""
    havdalah = config.get(HAVDALAH_MINUTES)
    latitude = config.get(CONF_LATITUDE, hass.config.latitude)
    longitude = config.get(CONF_LONGITUDE, hass.config.longitude)
    time_before = config.get(TIME_BEFORE_CHECK)
    time_after = config.get(TIME_AFTER_CHECK)

    if None in (latitude, longitude):
        _LOGGER.error("Latitude or longitude not set in Home Assistant config")
        return

    entities = []

    for resource in config[CONF_RESOURCES]:
        sensor_type = resource.lower()
        if sensor_type not in SENSOR_TYPES:
            SENSOR_TYPES[sensor_type] = [
                sensor_type.title(), '', 'mdi:flash']
        entities.append(Hebcal(hass, sensor_type, hass.config.time_zone, latitude, longitude,
                               havdalah, time_before, time_after))
                   
    async_add_entities(entities, False)


async def fetch(session, url):
    async with session.get(url) as response:
        return await response.text()


class Hebcal(Entity):
    """Create Hebcal sensor."""
    hebcal_db = []
    hebrew_date_db = None
    shabbat_in = None
    shabbat_out = None
    holiday_in = None
    holiday_out = None
    file_time_stamp = None
    friday = None
    config_path = None

    def __init__(self, hass, sensor_type, timezone, latitude, longitude,
                 havdalah, time_before, time_after):
        """Initialize the sensor."""
        self.type = sensor_type
        self.entity_id = async_generate_entity_id(
            ENTITY_ID_FORMAT,
            '_'.join([SENSOR_PREFIX, SENSOR_TYPES[self.type][2]]), hass=hass)
        self._latitude = latitude
        self._longitude = longitude
        self._timezone = timezone
        self._havdalah = havdalah
        self._time_before = time_before
        self._time_after = time_after
        self.config_path = hass.config.path() + "/custom_components/hebcal/"
        self._state = None
        self.parashat = None
        self.holiday_name = None

    @property
    def name(self):
        """Return the name of the sensor."""
        return SENSOR_PREFIX + SENSOR_TYPES[self.type][2]

    @property
    def icon(self):
        """Icon to use in the frontend, if any."""
        return SENSOR_TYPES[self.type][1]

    @property
    def should_poll(self):
        """Return true if the device should be polled for state updates"""
        return True

    @property
    def state(self):
        """Return the state of the sensor."""
        return self._state

    async def async_update(self):
        """Update our sensor state."""
        await self.update_db()
        type_to_func = {
            'shabbat_in': self.get_shabbat_time_in,
            'shabbat_out': self.get_shabbat_time_out,
            'is_shabbat': self.is_shabbat,
            'parasha': self.get_parasha,
            'holiday_in': self.get_holiday_time_in,
            'holiday_out': self.get_holiday_time_out,
            'is_holiday': self.is_holiday,
            'holiday_name': self.get_holiday_name,
            'hebrew_date': self.get_hebrew_date
        }
        self._state = await type_to_func[self.type]()
        await self.async_update_ha_state()

    async def create_db_file(self):
        """Create the json db."""
        self.set_days()
        self.hebcal_db = []
        self.parashat = None
        self.holiday_name = None
        self.shabbat_in = None
        self.shabbat_out = None
        self.holiday_in = None
        self.holiday_out = None
        self.file_time_stamp = datetime.date.today()
        self.hebcal_db.append({"update_date": str(self.file_time_stamp)})
        try:
            async with aiohttp.ClientSession() as session:
                html = await fetch(session,
                                   "https://www.hebcal.com/shabbat?cfg=json&gy="
                                   + str(self.friday.year) + "&gm=" + str(self.friday.month)
                                   + "&gd=" + str(self.friday.day) + "&geo=pos&latitude=" + str(self._latitude)
                                   + "&longitude=" + str(self._longitude) + "&tzid=" + str(self._timezone)
                                   + "&m=" + str(self._havdalah) + "&leyning=off&a=off")
                temp_db = json.loads(html)
                await self.filter_db(temp_db['items'], "new")
        except Exception as e:
            _LOGGER.error("Error in holiday create db : %s", str(e))
        try:
            async with aiohttp.ClientSession() as session:
                html = await fetch(session,
                                   "https://www.hebcal.com/hebcal/?v=1&cfg=json&year=now"
                                   "&month=" + str(self.friday.month) +
                                   "&maj=on&min=on&nx=on&mf=on&ss=on&mod=on&s=off&c=off&o=on&i=on&geo=pos"
                                   "&latitude=" + str(self._latitude) + "&longitude=" + str(self._longitude) +
                                   "&tzid=" + str(self._timezone) + "&m=" + str(self._havdalah) + "&lg=h")
                temp_db = json.loads(html)
                await self.filter_db(temp_db['items'], "new")
        except Exception as e:
            _LOGGER.error("Error in holiday events create db : %s", str(e))
        try:
            async with aiohttp.ClientSession() as session:
                html = await fetch(session,
                                   "https://www.hebcal.com/converter/?cfg=json&gy="
                                   + str(datetime.date.today().year) + "&gm=" + str(datetime.date.today().month)
                                   + "&gd=" + str(datetime.date.today().day) + "&g2h=1")
                self.hebcal_db.append(json.loads(html))
        except Exception as e:
            _LOGGER.error("Error in hebrew data: %s", str(e))
        with codecs.open(self.config_path + 'hebcal_data.json', 'w', encoding='utf-8') as outfile:
            json.dump(self.hebcal_db, outfile, skipkeys=False, ensure_ascii=False, indent=4,
                      separators=None, default=None, sort_keys=True)

    async def filter_db(self, temp_db, state):
        if state == "new":
            for extract_data in temp_db:
                if "date" in extract_data:
                    extract_data['date'] = extract_data['date'].replace("+03:00", "").replace("+02:00", "")
                if "candles" in list(extract_data.values()):
                    is_in = datetime.datetime.strptime(extract_data['date'], '%Y-%m-%dT%H:%M:%S')
                    if is_in.isoweekday() == 5:
                        self.shabbat_in = is_in
                    elif is_in.isoweekday() != 6 and is_in.isoweekday() != 5:
                        self.holiday_in = is_in
                    self.hebcal_db.append(extract_data)
                if "havdalah" in list(extract_data.values()):
                    is_out = datetime.datetime.strptime(extract_data['date'], '%Y-%m-%dT%H:%M:%S')
                    if is_out.isoweekday() >= 6:
                        self.shabbat_out = is_out
                    elif is_out.isoweekday() < 5:
                        self.holiday_out = is_out
                    self.hebcal_db.append(extract_data)
                if "parashat" in list(extract_data.values()):
                    self.parashat = extract_data['hebrew']
                    self.hebcal_db.append(extract_data)
                if any(x in ["holiday", "roshchodesh"] for x in list(extract_data.values())):
                    self.hebcal_db.append(extract_data)
            if self.shabbat_in and not self.shabbat_out:
                self.shabbat_out = self.shabbat_in + datetime.timedelta(days=1) + datetime.timedelta(minutes=65)
                self.hebcal_db.append({'hebrew': 'הבדלה - 42 דקות', 'start': str(self.shabbat_out).replace(" ", "T"),
                                       'className': 'havdalah', 'allDay': False, 'title': 'הבדלה - 42 דקות'})
            elif not self.shabbat_in and self.shabbat_out:
                self.shabbat_in = self.shabbat_out - datetime.timedelta(days=1) - datetime.timedelta(minutes=65)
                self.hebcal_db.append({'className': 'candles', 'hebrew': 'הדלקת נרות', 'start': str(self.shabbat_in)
                                      .replace(" ", "T"), 'allDay': False, 'title': 'הדלקת נרות'})
        elif state == "update":
            for extract_data in temp_db:
                if "date" in extract_data:
                    extract_data['date'] = extract_data['date'].replace("+03:00", "").replace("+02:00", "")
                if "candles" in list(extract_data.values()):
                    is_in = datetime.datetime.strptime(extract_data['date'], '%Y-%m-%dT%H:%M:%S')
                    if is_in.isoweekday() == 5:
                        self.shabbat_in = is_in
                    elif is_in.isoweekday() != 6 and is_in.isoweekday() != 5:
                        self.holiday_in = is_in
                if "havdalah" in list(extract_data.values()):
                    is_out = datetime.datetime.strptime(extract_data['date'], '%Y-%m-%dT%H:%M:%S')
                    if is_out.isoweekday() >= 6:
                        self.shabbat_out = is_out
                    elif is_out.isoweekday() < 5:
                        self.holiday_out = is_out
                if "parashat" in list(extract_data.values()):
                    self.parashat = extract_data['hebrew']

    async def update_db(self):
        """Update the db."""
        if not (pathlib.Path(self.config_path + 'hebcal_data.json').is_file()):
            await self.create_db_file()
        elif not self.hebcal_db or self.file_time_stamp is None:
            with open(self.config_path + 'hebcal_data.json', encoding='utf-8') as data_file:
                self.hebcal_db = json.loads(data_file.read())
                await self.filter_db(self.hebcal_db, "update")
            self.file_time_stamp = datetime.datetime.strptime(
                self.hebcal_db[0]['update_date'], '%Y-%m-%d').date()
        elif self.file_time_stamp != datetime.date.today():
            await self.create_db_file()

    @callback
    def set_days(self):
        """Set the friday and saturday."""
        weekday = self.set_friday(datetime.date.today().isoweekday())
        self.friday = datetime.date.today() + datetime.timedelta(days=weekday)

    @classmethod
    def set_friday(cls, day):
        """Set friday day."""
        switcher = {
            7: 5,
            1: 4,
            2: 3,
            3: 2,
            4: 1,
            5: 0,
            6: -1,
        }
        return switcher.get(day)

    # get shabbat entrace
    async def get_shabbat_time_in(self):
        """Get shabbat entrace."""
        if self.shabbat_in:
            return self.is_time_format(str(self.shabbat_in)[11:16])
        return self.shabbat_in

    # get shabbat time exit
    async def get_shabbat_time_out(self):
        """Get shabbat time exit."""
        if self.shabbat_out:
            return self.is_time_format(str(self.shabbat_out)[11:16])
        return self.shabbat_out

    # get shabbat entrace
    async def get_holiday_time_in(self):
        """Get shabbat entrace."""
        if self.holiday_in:
            return self.is_time_format(str(self.holiday_in)[11:16])
        return self.holiday_in

    # get shabbat time exit
    async def get_holiday_time_out(self):
        """Get shabbat time exit."""
        if self.holiday_out:
            return self.is_time_format(str(self.holiday_out)[11:16])
        return self.holiday_out

    # get parashat hashavo'h
    async def get_parasha(self):
        """Get parashat hashavo'h."""
        result = 'שבת מיוחדת'
        return self.parashat if self.parashat is not None else result

    # check if is shabbat now / return true or false
    async def is_shabbat(self):
        """Check if is shabbat now / return true or false."""
        if self.shabbat_in is not None and self.shabbat_out is not None:
            is_in = self.shabbat_in - datetime.timedelta(
                minutes=int(self._time_before))
            is_out = self.shabbat_out + datetime.timedelta(
                minutes=int(self._time_after))
            if is_in < datetime.datetime.today() < is_out:
                return 'True'
            return 'False'
        return 'False'

    # check if is holiday now / return true or false
    async def is_holiday(self):
        """Check if is holiday now / return true or false."""
        if self.holiday_in is not None and self.holiday_out is not None:
            is_in = self.holiday_in - datetime.timedelta(
                minutes=int(self._time_before))
            is_out = self.holiday_out + datetime.timedelta(
                minutes=int(self._time_after))
            if is_in < datetime.datetime.today() < is_out:
                return 'True'
            return 'False'
        return 'False'

    async def get_holiday_name(self):
        """Get holiday name."""
        result = self.heb_day_str()
        for extract_data in self.hebcal_db:
            if any(x in ["holiday", "roshchodesh"] for x in list(extract_data.values())):
                start = datetime.datetime.strptime(extract_data['date'][:10], '%Y-%m-%d').date()
                end = start + datetime.timedelta(days=1)
                if start <= datetime.date.today() < end:
                    result = result + " " + extract_data['hebrew']
                    return result
        return result

    # convert to hebrew date
    async def get_hebrew_date(self):
        """Convert to hebrew date."""
        day = self.heb_day_str()
        return day + self.hebcal_db[-1]['hebrew']

    @classmethod
    def heb_day_str(cls):
        """Set hebrew day."""
        switcher = {
            7: "יום ראשון, ",
            1: "יום שני, ",
            2: "יום שלישי, ",
            3: "יום רביעי, ",
            4: "יום חמישי, ",
            5: "יום שישי, ",
            6: "יום שבת, ",
        }
        return switcher.get(datetime.datetime.today().isoweekday())

    # check if the time is correct
    @classmethod
    def is_time_format(cls, input_time):
        """Check if the time is correct."""
        try:
            time.strptime(input_time, '%H:%M')
            return input_time
        except ValueError:
            return "Error"