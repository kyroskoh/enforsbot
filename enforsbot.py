#!/usr/bin/env python3
"enforsbot.py by Christer Enfors (c) 2015, 2016"


import eb_config, eb_thread, eb_queue, eb_message, eb_twitter, eb_telegram
import time, threading, re, socket, subprocess, sqlite3, datetime

#twitter_thread = eb_twitter.TwitterThread()

SYSCOND_DIR = "/home/enfors/syscond"


class EnforsBot:
    "The main class of the application."

    def __init__(self):
        self.config = eb_config.Config()
        
        # Responses are regexps.
        self.responses = {
            "ip"                 : self.respond_ip,
            "what.* ip .*"       : self.respond_ip,
            "what.*address.*"    : self.respond_ip,
            "ping"               : "Pong.",
            ".*good morning.*"   : "Good morning!",
            ".*good afternoon.*" : "Good afternoon!",
            ".*good evening.*"   : "Good evening!",
            ".*good night.*"     : "Good night!",
            "thank.*"            : "You're welcome.",
            "test"               : "I am up and running.",
            "LocationUpdate .*"  : self.handle_incoming_location_update,
            "locate"             : self.respond_location,
            "syscond"            : self.respond_syscond,
        }

        # Incoming user messages can come from several different threads.
        # When we get one, we keep track of which thread it's from, so
        # we know which thread we should send the response to. For example,
        # if we get a user message from TwitterStream, we should send the
        # response to TwitterRest.

        self.response_threads = {
            #Incoming from     Send response to
            #===============   ================
            "TwitterStreams" : "TwitterRest",
            "Telegram"       : "Telegram"
        }

        self.location = None
        self.arrived  = False

        self.db       = sqlite3.connect("enforsbot.db",
                                        detect_types = sqlite3.PARSE_DECLTYPES)


    def start(self):
        "Start the bot."
        self.start_all_threads()

        self.main_loop()


    def main_loop(self):
        "The main loop of the bot."
        while True:
            message = self.config.recv_message("Main")

            #print("Main: Incoming message from thread %s..." % message.sender)
            
            if message.msg_type == eb_message.MSG_TYPE_THREAD_STARTED:
                print("Thread started: %s" % message.sender)

            elif message.msg_type == eb_message.MSG_TYPE_USER_MESSAGE:
                self.handle_incoming_user_message(message,
                                                  self.response_threads[message.sender])

            elif message.msg_type == eb_message.MSG_TYPE_LOCATION_UPDATE:
                self.handle_incoming_location_update(message)
            else:
                print("Unsupported incoming message type: %d" % message.msg_type)
        

    def start_all_threads(self):
        "Start all necessary threads."
        with self.config.lock:

            twitter_thread = eb_twitter.TwitterThread(self.config)
            self.config.threads["Twitter"] = twitter_thread

            telegram_thread = eb_telegram.TelegramThread(self.config)
            self.config.threads["Telegram"] = telegram_thread

        twitter_thread.start()
        telegram_thread.start()


    def handle_incoming_user_message(self, message, response_thread):
        user = message.data["user"]
        text = message.data["text"]
        
        #print("Main: Message from %s: '%s'" % (user, text))

        response = "I'm afraid I don't understand."

        text = text.lower()

        for pattern in self.responses.keys():
            p = re.compile(pattern)

            if p.match(text):
                response = self.responses[pattern]

                if callable(response):
                    response = response(text)
                                        
        if response is not None:
            message = eb_message.Message("Main",
                                         eb_message.MSG_TYPE_USER_MESSAGE,
                                         { "user" : user,
                                           "text" : response })
            self.config.send_message(response_thread, message)


    def respond_ip(self, message):
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("gmail.com", 80)) # I'm abusing gmail. I'm sure it can take it.
        response = "I'm currently running on IP address %s." % s.getsockname()[0]
        s.close()

        return response


    def handle_incoming_location_update(self, message):
        user     = "Enfors" # Hardcoded for now. Sue me.
        location = message.data["location"]
        arrived  = message.data["arrived"]

        with self.config.lock, self.db:

            cur = self.db.cursor()

            if arrived:

                self.location = location
                self.arrived  = True

                cur.execute("insert into LOCATION_HISTORY "
                            "(user, location, event, time) values "
                            "(?, ?, 'arrived', ?)",
                            (user, location, datetime.datetime.now()))
                
                print("Main: Location updated: %s" % self.location)
                
            else: # if leaving
                
                # If leaving the location I'm currently at (sometimes the "left source"
                # message arrives AFTER "arrived at destination" message), skipping those.
                if self.arrived == False or location == self.location:

                    cur.execute("insert into LOCATION_HISTORY "
                                "(user, location, event, time) values "
                                "(?, ?, 'left', ?)",
                                (user, location, datetime.datetime.now()))
                    
                    print("Main: Location left: %s" % location)
                    self.arrived = False

        return None


    def respond_location(self, message):
        #if not self.location:
        #    return "There whereabouts of Enfors are currently unknown."

        #if self.arrived:
        #    return "Enfors was last seen arriving at %s." % self.location
        #else:
        #    return "Enfors was last seen leaving %s."     % self.location

        with self.db:

            cur = self.db.cursor()
            
            cur.execute("select * from LOCATION_HISTORY "
                        "order by ROWID desc limit 1")

            (user, location, event, timestamp) = cur.fetchone()

            if event == "arrived":
                return "%s %s at %s %s." % (user, event, location,
                                            self.get_datetime_diff_string(timestamp,
                                                                          datetime.datetime.now()))
            else:
                return "%s %s %s %s." % (user, event, location,
                                         self.get_datetime_diff_string(timestamp,
                                                                       datetime.datetime.now()))


    def respond_syscond(self, message):
        return self.check_syscond()


    def check_syscond(self):
        syscond_output = subprocess.Popen(["syscond", "status", "-n"],
                                          stdout=subprocess.PIPE).communicate()[0]

        return syscond_output.decode("utf-8")


    def get_datetime_diff_string(self, d1, d2):

        if d1 > d2:
            return "in the future"

        diff = d2 - d1

        total_seconds = diff.total_seconds()

        minutes = total_seconds // 60
        hours   = total_seconds // 60 // 60
        days    = total_seconds // 60 // 60 // 24

        if days:
            hours -= (days * 24)
            
            return "%d %s, %d %s ago" % (days,    self.get_possible_plural("day",    days),
                                         hours,   self.get_possible_plural("hour",   hours))
        elif hours:
            minutes -= (hours * 60)

            return "%d %s, %d %s ago" % (hours,   self.get_possible_plural("hour",   hours),
                                         minutes, self.get_possible_plural("minute", minutes))
            
        elif minutes:
            return "%d %s ago" % (minutes, self.get_possible_plural("minute", minutes))
        
        else:
            return "just now"


    def get_possible_plural(self, word, num):
        if num == 1:
            return word
        else:
            return word + "s"
        
            
def main():
    "Start the application."
    app = EnforsBot()
    app.start()
    

if __name__ == "__main__":
    main()