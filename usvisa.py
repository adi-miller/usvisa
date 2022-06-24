import sys
import json
import logging
import requests
import optparse
import threading
from lxml import html
from time import sleep
from datetime import datetime

class TooManyRequestsException(Exception):
    pass

class OtherHttpException(Exception):
    pass

class BlockedException(Exception):
    pass

class USVisa:
    def __init__(self, username, password, scheduleId, bestDate, locations, delay, delaytmr):
        self.logger = self.getLogger()
        self.scheduleId = scheduleId
        self.username = username
        self.password = password
        self.scheduleId = scheduleId
        self.bestDate = bestDate
        self.locations = locations
        self.delay = delay
        self.delaytmr = delaytmr

    def getLogger(self):
        formatter = logging.Formatter(fmt='%(asctime)s %(levelname)-8s %(message)s', datefmt='%Y-%m-%d %H:%M:%S')
        handler = logging.FileHandler('log.txt', mode='w')
        handler.setFormatter(formatter)
        screen_handler = logging.StreamHandler(stream=sys.stdout)
        screen_handler.setFormatter(formatter)
        logger = logging.getLogger("MyLogger")
        logger.setLevel(logging.DEBUG)
        logger.addHandler(handler)
        logger.addHandler(screen_handler)
        return logger

    def login(self, session_requests):
        login_url = "https://ais.usvisa-info.com/en-il/niv/users/sign_in"
        result = session_requests.get(login_url, headers={'User-Agent': 'Mozilla/5.0'})
        tree = html.fromstring(result.text)
        authenticity_token = list(set(tree.xpath("//input[@name='authenticity_token']/@value")))[0]
        payload = {
            "user[email]": self.username,
            "user[password]": self.password,
            "policy_confirmed": "1",
            "authenticity_token": authenticity_token
        }
        result = session_requests.post(login_url, data=payload, headers={'User-Agent': 'Mozilla/5.0'})
        return result, session_requests
        
    def findEarliest(self, session_requests, loc):
        res2 = session_requests.get(f"https://ais.usvisa-info.com/en-il/niv/schedule/{self.scheduleId}/appointment/days/{loc}.json?appointments[expedite]=false", headers={'User-Agent': 'Mozilla/5.0'})
        if res2.status_code == 200:
            jsonRes = json.loads(res2.text)
            if len(jsonRes) == 0:
                raise BlockedException()
            for res in jsonRes:
                aDate = datetime.strptime(res["date"], "%Y-%m-%d")
                if aDate >= datetime(year=2022, month=7, day=11):
                    return aDate
            raise BlockedException()
        elif res2.status_code == 429:
            raise TooManyRequestsException()
        else:
            raise OtherHttpException()
        
    def findAvailableTimes(self, session_requests, loc, dateStr, timeSlots):
        url = f"https://ais.usvisa-info.com/en-il/niv/schedule/{self.scheduleId}/appointment/times/{loc}.json?date={dateStr}&appointments[expedite]=false"
        _delay = 0.5
        for i in range(9):
            self.logger.info(f"Finding time for {dateStr} at {loc}...")
            res = session_requests.get(url, headers={'User-Agent': 'Mozilla/5.0'})
            self.logger.debug(f"Status code: {res.status_code}")
            if res.status_code == 200:
                jsonRes = json.loads(res.text)
                self.logger.debug(f"Available times: {res.text}")
                for time in jsonRes["available_times"]:
                    timeSlots.insert(0, time)
                return 
            if res.status_code != 429:
                raise OtherHttpException()
            self.logger.debug(f"[Finding time] Sleeping for {_delay}...")
            sleep(_delay)
            _delay = _delay * 1.7
        self.logger.warning("Couldn't find times after all retries.")

    def doReschedulePost(self, timeSlots, url, payload, hdrs, session_requests):
        for timeStr in timeSlots:
            _delay = 0.1
            for i in range(9):
                self.logger.info(f"Sending reschedule request at {timeStr}...")
                payload["appointments[consulate_appointment][time]"] = timeStr
                res = session_requests.post(url, data=payload, headers=hdrs)
                self.logger.debug(f"Status code: {res.status_code}")
                try:
                    dateStr = payload["appointments[consulate_appointment][date]"]
                    _filename = f"reschedule{dateStr}-{timeStr}.html"
                    _filename.replace(':', '').replace('-', '')
                    f = open(_filename, "w")
                    f.write(res.text)
                    f.close
                except Exception as ex:
                    self.logger.error(f"Couldn't write file: {ex}")
                    self.logger.debug(res.text)
                    pass
                if res.status_code == 429:
                    self.logger.debug(f"[Sending reschedule request] Sleeping for {_delay}...")
                    sleep(_delay)
                    _delay = _delay * 1.7
                elif res.status_code == 200:
                    if res.text.find('Successfully Scheduled') != -1:
                        self.logger.info(f"Success!")
                        return True
                    else:
                        self.logger.warning(f"Failed")
                        continue
                elif res.status_code != 429:
                    raise OtherHttpException()
        return False
                    
    def reschedule(self, session_requests, loc, dateStr):
        try:
            timeSlots = []
            findTimeThread = threading.Thread(target=self.findAvailableTimes, args=(session_requests, loc, dateStr, timeSlots))
            findTimeThread.start()
            self.logger.info(f"Trying to reschedule for {dateStr} at {loc}...")
            _delay = 0.1
            for i in range(9):
                self.logger.debug(f"Getting reschedule page...")
                url = f"https://ais.usvisa-info.com/en-il/niv/schedule/{self.scheduleId}/appointment"
                getRes = session_requests.get(url, headers={'User-Agent': 'Mozilla/5.0'})
                if getRes.status_code == 200:
                    tree = html.fromstring(getRes.text)
                    findTimeThread.join()
                    hdrs = {
                        "User-Agent": "Mozilla/5.0", 
                        "Cookies": getRes.headers["Set-Cookie"],
                        "Referer": url
                    }
                    payload = {
                        "authenticity_token": list(set(tree.xpath("//input[@name='authenticity_token']/@value")))[0],
                        "confirmed_limit_message": "1",
                        "use_consulate_appointment_capacity": "true",
                        "appointments[consulate_appointment][facility_id]": loc,
                        "appointments[consulate_appointment][date]": dateStr,
                        "appointments[consulate_appointment][time]": "",
                    }
                    reschedRes = self.doReschedulePost(timeSlots, url, payload, hdrs, session_requests)
                    return reschedRes

                elif getRes.status_code != 429:
                    raise OtherHttpException()
                self.logger.debug(f"[Getting reschedule page] Sleeping for {_delay}...")
                sleep(_delay)
                _delay = _delay * 1.7
        except Exception as ex:
            self.logger.warning(f"Couldn't reschedule: {ex}")
            return False
        
    def hunt(self):
        session_requests = requests.session()
        while True:
            self.logger.info("Logging in...")
            res, session_requests = self.login(session_requests)
            if res.status_code != 200:
                self.logger.warning(f"Login failed. Status_code: {res.status_code}.")
                sleep(self.delaytmr)
            else:
                counter = 0
                while True:
                    try:
                        for loc in self.locations:
                            earliestDate = self.findEarliest(session_requests, loc)
                            counter = counter + 1
                            ordinal = earliestDate.toordinal() - datetime.now().toordinal()
                            shortStr = earliestDate.strftime("%Y-%m-%d")
                            self.logger.debug(f"Attempt #{counter:4}. Earliest found: {shortStr} (in {ordinal} days) @ {loc}")
                            if earliestDate < self.bestDate:
                                rescheduleRes = self.reschedule(session_requests, loc, shortStr)
                                if rescheduleRes:
                                    self.bestDate = earliestDate
                            sleep(self.delay)
                    except TooManyRequestsException as ex:
                        self.logger.debug("Too many requests. Sleeping...")
                        sleep(self.delaytmr)
                    except BlockedException as ex:
                        self.logger.warning(f"Blocked. Waiting... ")
                        sleep(60*60*4)
                    except OtherHttpException as ex:
                        self.logger.warning(f"Something else. Error: {ex}. ")
                        break
                    except Exception as ex:
                        self.logger.warning(f"Exception: {ex}")
                        break

def main(argv):
    parser = optparse.OptionParser()
    parser.add_option("-u", dest="username", help="Username for the AIS account", metavar="johndoe@gmail.com")
    parser.add_option("-p", dest="password", help="Password to the AIS account", metavar="4vrhef34erf")
    parser.add_option("-s", dest="scheduleId", help="A number representing the schedule ID of your request", metavar="32835621")
    parser.add_option("-c", dest="currentDate", help="The current date of the appointment in yyyy-mm-dd format", metavar="2023-03-24")
    parser.add_option("-l", dest="locations", help="Consular section locations. Use F12 on the Schedule Appointments screen. Support multiple values", metavar="96,97")
    parser.add_option("-i", "--delay", dest="delay", help="Seconds to wait between requests", default=3)
    parser.add_option("-t", "--delaytmr", dest="delaytmr", help="Seconds to wait after TooManyRequests (429) before retry", default=10)
    try:
        (options, args) = parser.parse_args()
    
        if options.username is None or options.password is None or options.scheduleId is None or options.currentDate is None or options.locations is None:
            raise optparse.OptionError("Missing required options", "")
    
        usvisa = USVisa(options.username, options.password, options.scheduleId, datetime.strptime(options.currentDate, "%Y-%m-%d"), options.locations.split(","), int(options.delay), int(options.delaytmr))
        usvisa.hunt()
    except optparse.OptionError as ex:
        parser.print_help()
if __name__ == '__main__':
    sys.exit(main(sys.argv))
