import configparser
import dataset
import datetime
import logging

from telegram import ParseMode
from telegram.ext import Updater, CommandHandler, MessageHandler, Filters

from dateutil.parser.isoparser import isoparser

import corona_data_crawler

# =================================================================================================

logging.basicConfig(
  format="%(asctime)s - %(name)s - %(levelname)s : %(message)s",
  level=logging.INFO
)
log = logging.getLogger(__file__)

# =================================================================================================


class CoronaReporterBot():
    """
    A Telegram bot that (1) periodically crawls the excel file provided by the "Sozialministerium
    Baden Württemberg" for the Corona infection- and death rates in Baden-Württemberg (BW) and
    its counties; and (2) periodically sends a report to all subscribed chats (Telegram uses the
    term "chats" to describe "users", so we will use this term throughout this project as well),
    giving an overview of e.g., today's infection- and death rates in BW and selected counties, and
    a comparison to yesterday's rates.
    """

    def __init__(self, config):
        """
        Creates a new instance of this Telegram bot.

        Params:
            config (dict):
                The configuration for this bot.
        """
        # Configure the Telegram API.
        self.telegram_api_key = config["default"]["telegram_api_key"]
        self.admin_chat_id = config["default"]["admin_chat_id"]

        # Configure the database.
        self.db_host = config["db"]["host"]
        self.db_table_name_subscribed_chats = config["db"]["table_name_subscribed_chats"]
        self.db_table_name_reports = config["db"]["table_name_reports"]

        # Configure the crawler.
        self.crawler_excel_file_url = config["crawler"]["excel_file_url"]
        self.crawler_start_time_str = config["crawler"]["start_time"]

        # Configure the reporter.
        self.reporter_include_counties = config["reporter"]["include_counties"]
        self.reporter_start_time_str = config["reporter"]["start_time"]

    def start(self):
        """
        Starts this bot and listens for new updates from the subscribed chats.
        """
        # Connect to the database.
        db = dataset.connect(self.db_host)
        # Establish a table for storing the subscribed chats.
        self.db_subscribed_chats = db[self.db_table_name_subscribed_chats]
        # Establish a table for storing the reports to be sent to the subscribed chats.
        self.db_reports = db[self.db_table_name_reports]

        # The updater for receiving updates (e.g., commands/messages sent from a chat to the bot).
        self.updater = Updater(self.telegram_api_key, use_context=True)

        # The dispatcher for registering our command handlers and handling the received updates.
        self.dispatcher = self.updater.dispatcher
        self.dispatcher.add_handler(CommandHandler("start", self.handle_start_command))
        self.dispatcher.add_handler(CommandHandler("stop", self.handle_stop_command))
        self.dispatcher.add_handler(CommandHandler("crawl", self.handle_crawl_command))
        self.dispatcher.add_handler(CommandHandler("report", self.handle_report_command))
        self.dispatcher.add_handler(CommandHandler("help", self.handle_help_command))
        self.dispatcher.add_handler(MessageHandler(Filters.text, self.handle_message))
        self.dispatcher.add_error_handler(self.handle_error)

        #  The ISO parser for parsing the start time of the crawler & reporter given in the config.
        iso_parser = isoparser()

        # Create a job that periodically creates the report (and crawls the required data).
        self.crawler_start_time = iso_parser.parse_isotime(self.crawler_start_time_str)
        self.updater.job_queue.run_daily(self.crawler_job, self.crawler_start_time)

        # Create a job that periodically sends the report to all subscribed chats.
        self.reporter_start_time = iso_parser.parse_isotime(self.reporter_start_time_str)
        self.updater.job_queue.run_daily(self.reporter_job, self.reporter_start_time)

        # Start the bot.
        self.log_event("Starting the bot ...")
        self.updater.start_polling()

        # Run the bot until it receives SIGINT, SIGTERM or SIGABRT. This should be used most of
        # the time, since start_polling() is non-blocking and will stop the bot gracefully.
        self.log_event("Listening for new updates ...")
        self.updater.idle()

    # ---------------------------------------------------------------------------------------------
    # Define the command handlers.

    def handle_start_command(self, update, context):
        """
        Handles a /start command, sent by a chat when it subscribes to our bot.
        """
        chat = update.message.chat

        # Only take the information of the chat that we are interested in.
        our_chat = {
          "id": chat.id,
          "title": chat.title,
          "username": chat.username,
          "first_name": chat.first_name,
          "last_name": chat.last_name,
          # Define a greeting name, either consisting of the first name (if any) or the last name.
          "greeting_name": chat.first_name if chat.first_name else chat.last_name
        }

        # Add the chat to the "subscribed chats" table, regardless whether it was already added
        # (if the table already contains an entry with the same chat id, it will be overwritten).
        self.db_subscribed_chats.upsert(our_chat, keys=["id"])

        # Send a response to the user.
        greeting = our_chat["greeting_name"]
        report_hour = self.reporter_start_time.hour
        report_minute = self.reporter_start_time.minute
        update.message.reply_html(
          f"Hey {greeting}, danke für deine Anmeldung 🥳. Ich sende dir ab sofort täglich um "
          f"{report_hour}:{report_minute:02} Uhr einen Bericht mit den aktuellen Corona "
          f"Infektions- und Todesfällen in Freiburg und Baden-Württemberg. Wenn du die Berichte "
          f"nicht mehr erhalten willst, tippe einfach /stop."
        )
        self.log_event(f"Chat {our_chat} subscribed.", notify_admin=True)

    def handle_stop_command(self, update, context):
        """
        Handles a /stop command, which is sent by a chat when it unsubscribes from our bot.
        """
        # Remove the chat from the "subscribed chats" table.
        self.db_subscribed_chats.delete(id=update.message.chat.id)

        # Send a response to the user.
        update.message.reply_text(
          "Ok, ich sende dir ab sofort keine Corona-Berichte mehr. Wenn du die Berichte wieder "
          "erhalten möchtest, tippe einfach /start."
        )
        self.log_event(f"Chat {update.message.chat} <b>un</b>subscribed.", notify_admin=True)

    def handle_crawl_command(self, update, context):
        """
        Handles a /crawl command, which is sent by a chat when it starts the crawling manually.
        """
        self.crawler_job(context)
        update.message.reply_html("Ok.")
        self.log_event(f"Chat {update.message.chat} started crawling manually.", notify_admin=True)

    def handle_report_command(self, update, context):
        """
        Handles a /report command, which is sent by a chat when it requests the report manually.
        """
        # Send the latest report to the *single* user.
        self.send_report(context, [update.message.chat])
        self.log_event(f"Chat {update.message.chat} requested report manually.", notify_admin=True)

    def handle_help_command(self, update, context):
        """
        Handles a /help command, which is sent by a chat when it requests the help message.
        """
        help_lines = [
          "Tippe:",
          "/start, um dich für den täglichen Corona-Bericht anzumelden;",
          "/help, um diese Hilfe-Nachricht erneut anzuzeigen;",
          "/stop, um dich von den täglichen Corona-Berichten abzumelden;",
          "/report, um den aktuellen Corona-Bericht anzuzeigen."
        ]
        update.message.reply_html("\n".join(help_lines))
        self.log_event(f"Chat {update.message.chat} requested help message.", notify_admin=True)

    def handle_message(self, update, context):
        """
        Handles an incoming message which is not a command or an unknown command.
        """
        message = update.message
        # Send a response to the user.
        message.reply_text(
          "Entschuldige, das habe ich nicht verstanden. Ich bin leider ein ziemlich dummer Bot "
          "und verstehe deshalb nur die Kommandos die mir mein Programmierer beigebracht hat und "
          "unter /help aufgelistet sind."
        )
        self.log_event(f"Chat {message.chat} sent message: {message.text}", notify_admin=True)

    def handle_error(self, update, context):
        """
        Handles any error caused by an update.
        """
        update.message.reply_text(
          "Ups, das hat nicht geklappt. Keine Sorge, das ist nicht deine Schuld, sondern die von "
          "meinen unfähigen Programmierer 🙄. Ich habe ihn gerade über diesen Fehler informiert, "
          "damit er ihn schnellstmöglich beheben kann."
        )
        self.log_error(update, context.error, notify_admin=True)

    # =============================================================================================
    # The daily jobs.

    def crawler_job(self, context):
        """
        A job that (1) crawls the excel file for the current infection- and death rates and (2)
        creates the report to be sent to the subscribed chats.
        """
        # Crawl the excel file.
        infections, deaths = corona_data_crawler.crawl(self.crawler_excel_file_url)

        # Create the report.
        report_date, report = self.create_html_report(infections, deaths)

        # Store the report to db.
        report_obj = {"date": report_date.strftime("%Y-%m-%d"), "report": report}
        self.db_reports.upsert(report_obj, keys=["date"])

    def reporter_job(self, context):
        """
        A job that sends the latest report to *all* subscribed chats.
        """
        self.send_report(context, self.db_subscribed_chats)

    # =============================================================================================

    def create_html_report(self, infections, deaths):
        """
        Creates an html report containing the latest statistics.
        """
        report_date = datetime.date.today()

        # Retrieve the current date.
        report_date_str = report_date.strftime("%d.%m.%Y")

        self.log_event("Composing html report ...")
        lines = [
          f"Dein täglicher Corona-Statusbericht vom {report_date_str}:",
          ""  # empty line.
        ]

        # Create an report entry for each county to include.
        include_counties = self.reporter_include_counties.split(",")
        for county in include_counties:
            county_infections = infections.get(county)
            county_deaths = deaths.get(county)

            if not county_infections and not county_deaths:
                continue

            lines.append(f"<b>{county}:</b>")

            # Infections.
            if county_infections:
                today = county_infections[0]
                yday = county_infections[1]
                delta = today - yday
                lines.append(f"• <b>{delta:+.0f}</b> Neuinfektionen ({today:.0f} « {yday:.0f})")

            # Deaths.
            if county_deaths:
                today = county_deaths[0]
                yday = county_deaths[1]
                delta = today - yday
                lines.append(f"• <b>{delta:+.0f}</b> Todesfälle ({today:.0f} « {yday:.0f})")

            # Separate the report entries by an empty line.
            lines.append("")

        lines.append("Bleib gesund! 😷")

        return report_date, "\n".join(lines)

    def send_report(self, context, chats):
        """
        Sends the latest report to each given chat.
        """
        # Fetch the latest report from db.
        num_reports = self.db_reports.count()
        report_obj = next(self.db_reports.find(order_by=["-date"])) if num_reports > 0 else None
        report = report_obj["report"] if report_obj is not None else None

        # Send the report to each char.
        for chat in chats:
            self.log_event(f"Sending report to {chat}.", notify_admin=True)
            if report:
                context.bot.send_message(chat["id"], report, parse_mode=ParseMode.HTML)
            else:
                context.bot.send_message(chat["id"], "No report available.")

    # =============================================================================================
    # Log methods.

    def log_event(self, event, notify_admin=False):
        """
        Logs the given event. Prints a log entry to the console and, if notify_admin is set to
        True, sends the event message to the Telegram chat of the specified admin account.
        """
        log.info(event)
        if notify_admin and self.admin_chat_id:
            self.updater.bot.send_message(
                chat_id=self.admin_chat_id,
                text=f"<code>{event}</code>",
                parse_mode=ParseMode.HTML
            )

    def log_error(self, update, error, notify_admin=False):
        """
        Logs the given error. Prints a log entry to the console and, if notify_admin is set to
        True, sends the error message to the Telegram chat of the specified admin account.
        """
        message = f"Error: {error}; Update: {update}."
        log.exception(message)
        if notify_admin and self.admin_chat_id:
            self.updater.bot.send_message(
                chat_id=self.admin_chat_id,
                text=f"<code>{message}</code>",
                parse_mode=ParseMode.HTML
            )

# =================================================================================================


if __name__ == "__main__":
    # TODO: Read the config from command line.
    config = configparser.ConfigParser()
    config.read("./config.ini")

    CoronaReporterBot(config).start()
