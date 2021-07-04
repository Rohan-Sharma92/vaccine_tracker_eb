from multiprocessing.queues import Queue
from telegram.ext.callbackqueryhandler import CallbackQueryHandler
from flask import Flask, request
import concurrent.futures
import os
from threading import Thread
import logging, requests, json, schedule, threading
import polling2, queue
from telegram import ReplyKeyboardMarkup, ReplyKeyboardRemove, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Updater,
    CommandHandler,
    MessageHandler,
    Filters,
    ConversationHandler,
    CallbackQueryHandler,
    CallbackContext,
)
from utils import telegramcalendar,Counter
from datetime import datetime
from polling2 import MaxCallException
from telegram.ext.messagehandler import MessageHandler
from builtins import set

"""
A telegram bot to check vaccination centers for a given pincode and location
"""

# Enable logging
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
                    level=logging.INFO)

logger = logging.getLogger(__name__)

SELECTING_ACTION, SELECTING_PIN, SELECTING_LOCATION, SELECT_DATE, SELECT_DOSE,DOSE,PIN, LOCATION, DATE = map(chr, range(9))
dict = {"1":PIN, "2":LOCATION}
END = ConversationHandler.END

responseQueue = queue.SimpleQueue()

application = Flask(__name__)
cache = {}
chat_cache = {}
chat_requestCache={}

class ResponseBuffer(object):
    counter=None
    text=None
    chat_id = None
    vaccines=None
    def __init__(self, counter,chat_id):
        self.counter=counter
        self.chat_id=chat_id
        self.vaccines={}
        self.text=""
        
    def process(self):
        self._lock = threading.Lock()
        with self._lock:
            if(self.counter.value!=0):
                return None
            self.counter.dec();
            txt=""
            for key, value in self.vaccines.items():
               msg = "\n" + key + ":\n\n"
               msg += "\n".join(value) + "\n"
               txt += msg
            self.text=txt
        return txt

class RequestParams(object):
    pin = ""
    date = None
    latitude = None
    longitude = None
    type = None
    chat_id = None
    dose=None
    # The class "constructor" - It's actually an initializer 
    def __init__(self, chat_id, type, pin=None, date=None,dose=None, latitude=None, longitude=None):
        self.type = type
        self.pin = pin
        self.date = date
        self.dose=dose
        self.longitude = longitude
        self.latitude = latitude
        self.chat_id = chat_id

        
class Response(object):
    json_obj = None
    type = None
    chat_id = None
    text = ""
    req=None

    # The class "constructor" - It's actually an initializer 
    def __init__(self, chat_id, type, json_obj,req:RequestParams):
        self.type = type
        self.json_obj = json_obj
        self.chat_id = chat_id
        self.req=req
        
    def updateText(self, text:str):
        self.text+= text


# Define a few command handlers. These usually take the two arguments update and
# context. Error handlers also receive the raised TelegramError object in error.
def start(update, context) -> None:
    """Send a message when the command /start is issued."""
    buttons = [
        [
            InlineKeyboardButton(text='Search via Pin Code', callback_data=str(PIN)),
            InlineKeyboardButton(text='Search via Location', callback_data=str(LOCATION)),
        ],
        [
            InlineKeyboardButton(text='Already vaccinated !', callback_data=str(END)),
        ],
    ]

    keyboard = InlineKeyboardMarkup(buttons)
    update.message.reply_text('Hello !\nWelcome to Vaccine Tracker\nPlease choose:', reply_markup=keyboard)
    return SELECTING_ACTION


def help(update, context):
    """Send a message when the command /help is issued."""
    update.message.reply_text('Help!')


def cancel(update: Update, _: CallbackContext) -> int:
    user = update.message.from_user
    logger.info("User %s canceled the conversation.", user.first_name)
    update.message.reply_text(
        'Bye! ', reply_markup=ReplyKeyboardRemove()
    )

    return ConversationHandler.END



def addDosageSelection(update):
    buttons = [
        [
            InlineKeyboardButton(text='Dose 1', callback_data=str(1)), 
            InlineKeyboardButton(text='Dose 2', callback_data=str(2))], 
        [
            InlineKeyboardButton(text='skip', callback_data=str(-1))]]
    keyboard = InlineKeyboardMarkup(buttons)
    return keyboard

def date_complete(update:Update,_:CallbackContext)->int:
    user = update.message.from_user
    logger.info("Date provided by %s: %s", user.first_name, _.user_data['date'])
    addDosageSelection(update)
    update.message.reply_text('Please choose:', reply_markup=keyboard)
    return SELECT_DOSE

def select_dose(update: Update, context: CallbackContext) -> int:
    text = 'Do you wish to be notified ? (Y/N)'
    context.user_data['dose'] = int(update.callback_query.data)
    update.callback_query.answer()
    update.callback_query.edit_message_text(text=text)
    return DOSE

def getPinCodes(res):
    json_obj = res.json_obj
    logger.info("Response: %s", json_obj)
    if json_obj is not None:
        s1 = json.dumps(json_obj)
        result = json.loads(s1)
    pincodes = set()
    for details in result['centers']:
        pincodes.add(details['pincode'])
    return pincodes

def dose(update: Update, _: CallbackContext)-> int:
    user = update.message.from_user
    logger.info("Dose selected by %s: %s", user.first_name, update.message.text)
    notify=update.message.text
    if(notify in ('n','N')):
        return vaccinated(update,_)
    update.message.reply_text(
            'Thanks, we will notify you as soon as a slot is available for this date.\nTo unsubscribe, type in /vaccinated',
        reply_markup=ReplyKeyboardRemove(),
    )
    user = update.message.from_user
    type = _.user_data['requestType']
    dose = _.user_data['dose']
    params = None
    chat_id = update.effective_chat.id;
    if(type == 'LOCATION'):
        params = createLocationParams(_, chat_id, type,dose)
        res=checkDetails(params)
        pincodes=getPinCodes(res)
        completeWithPin(update, _, pincodes)
    else:
        complete(update, _)
    return ConversationHandler.END


def choose_date(update):
    return update.message.reply_text("Please select a date: ", 
        reply_markup=telegramcalendar.create_calendar())

def pin(update: Update, _: CallbackContext)-> int:
    user = update.message.from_user
    _.user_data['requestType'] = 'PIN'
    _.user_data['pin'] = update.message.text
    logger.info("Pin of %s: %s", user.first_name, update.message.text)
    choose_date(update)
    return SELECT_DATE

def select_date(update: Update, context: CallbackContext)->int:
    selected,date = telegramcalendar.process_calendar_selection(context.bot, update)
    if selected:
        text="Please choose:"
        context.user_data['date']=date.strftime("%d-%m-%Y")
        update.callback_query.answer()
        keyboard=addDosageSelection(update)
        update.callback_query.edit_message_text(text=text,reply_markup=keyboard)
        return SELECT_DOSE
    else:
        SELECT_DATE

def location(update: Update, _: CallbackContext) -> int:
    user = update.message.from_user
    _.user_data['requestType'] = 'LOCATION'
    _.user_data['location'] = update.message.location
    logger.info("Location of %s: %s - %s", user.first_name, update.message.location.latitude, update.message.location.longitude)
    # keyboard=addDosageSelection(update)
    # update.message.reply_text('Please choose:', reply_markup=keyboard)
    choose_date(update)
    return SELECT_DATE

def capacity(details,dose)->int:
    field=None
    if dose==1:
        field= "available_capacity_dose1"
    elif dose==2:
        field= "available_capacity_dose2"
    else:
        field="available_capacity"
    return int(details[field])

def isSuccess(res: Response) -> bool:
    found = False;
    json_obj = res.json_obj
    logger.info("Response: %s", json_obj)
    vaccineDetails = {}
    centreDetails = []
    dose=res.req.dose
    if json_obj is not None:
        s1 = json.dumps(json_obj)
        result = json.loads(s1)
        key = 'sessions'
        for details in result[key]:
            availableCapacity=capacity(details,dose)
            key = details['vaccine']
            if(key not in vaccineDetails):
                vaccineDetails[key] = []
            if(availableCapacity > 0):
                vaccineDetails[key].append(details['name'])
            found = True
    if found:
        text = ""
        buf=chat_requestCache[res.chat_id]
        buf.counter.dec()
        for key, value in vaccineDetails.items():
            if( key not in buf.vaccines):
                buf.vaccines[key]=set()
                buf.vaccines[key].update(value)
        if(buf.counter.value==0):
            msg=buf.process()
            if(msg is not None):
                buf.text=msg
                responseQueue.put_nowait(buf)
    return found


def createPinCodeParams(context, chat_id, type,dose) -> RequestParams:
    pin = context.user_data['pin']
    date = context.user_data['date']
    params = RequestParams(chat_id, type, pin=pin, date=date,dose=dose)
    return params


def createLocationParams(context, chat_id, type,dose) -> RequestParams:
    location = context.user_data['location']
    params = RequestParams(chat_id, type,dose=dose, latitude=location.latitude, longitude=location.longitude)
    return params


def cleanup(params):
    updater = chat_cache[params.chat_id]
    if updater is not None:
         updater.message.reply_text('Thanks for using this service. No vacant schedule found for your query. Please try again later', reply_markup=ReplyKeyboardRemove(),)
         chat_cache.pop(params.chat_id)

    
def poll(params):
    try:
        polling2.poll(target=lambda:checkDetails(params), check_success=isSuccess, step=10,
        max_tries=6,
        timeout=3600,
        log=logging.DEBUG)
    except MaxCallException:
        cleanup(params)


def completeWithPin(update: Update, context: CallbackContext,pincodes):
    user = update.message.from_user
    dose = context.user_data['dose']
    chat_id = update.effective_chat.id;
    today=datetime.now().strftime("%d-%m-%Y")
    params=[]
    for pin in pincodes:
        params.append(RequestParams(chat_id, "PIN", pin=pin, date=today,dose=dose))
    logger.info("Nominated person: %s, Parameters: %s", user.first_name, params)
    chat_requestCache[chat_id]=ResponseBuffer(Counter.AtomicCounter(initial=len(pincodes)),chat_id)
    polling_executor = context.dispatcher.user_data['polling_executor']
    if(chat_id not in chat_cache):
        chat_cache[chat_id] = update
    for req in params:
        polling_executor.submit(lambda:poll(req))

def complete(update: Update, context: CallbackContext):
    user = update.message.from_user
    type = context.user_data['requestType']
    dose = context.user_data['dose']
    params = None
    chat_id = update.effective_chat.id;
    if(type == 'PIN'):
        params = createPinCodeParams(context, chat_id, type,dose)
    else:
        params = createLocationParams(context, chat_id, type,dose)
    chat_requestCache[chat_id]=ResponseBuffer(Counter.AtomicCounter(initial=1),chat_id)
    logger.info("Nominated person: %s, Parameters: %s", user.first_name, params)
    polling_executor = context.dispatcher.user_data['polling_executor']
    polling_executor.submit(lambda:poll(params))
    if(chat_id not in chat_cache):
        chat_cache[chat_id] = update
    
def run_threaded(job_func, name) -> Thread:
    job_thread = threading.Thread(target=job_func, name=name)
    return job_thread


def sendMessage():
    res = "Following centres are available:\n"
    while True:
        response = responseQueue.get(block=True)
        res += response.text
        update = chat_cache[response.chat_id]
        if update is not None:
            update.message.reply_text(res,
                                      reply_markup=ReplyKeyboardRemove(),)
        chat_cache.pop(response.chat_id)
        chat_requestCache.pop(response.chat_id)
        res="Following centres are available:\n"
        

def vaccinated(update: Update, _: CallbackContext) -> int:
    user = update.message.from_user
    logger.info("Vaccinated person: %s", user.first_name)
    update.message.reply_text(
        'Thanks for using this service !',
        reply_markup=ReplyKeyboardRemove(),
    )
    return ConversationHandler.END


def checkDetails(param: RequestParams):
    """Echo the user message."""
    type = param.type
    if(type == 'PIN'):
        return makePinCodeRequest(param)     
    else:
        return makeLocationRequest(param)


def makeHeader() -> str:
    header = {'Accept-Language': 'hi_IN', 'Accept': 'application/json', 'user-agent':'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/90.0.4430.93 Safari/537.36'}
    return header


def makePinCodeRequest(param:RequestParams):
    pload = {'pincode':param.pin, 'date':param.date}
    response = requests.get('https://cdn-api.co-vin.in/api/v2/appointment/sessions/public/findByPin', params=pload, headers=makeHeader())
    res = Response(param.chat_id, param.type, response.json(),param)
    return res;


def makeLocationRequest(param:RequestParams):
    pload = {'lat':param.latitude, 'long':param.longitude}
    response = requests.get('https://cdn-api.co-vin.in/api/v2/appointment/centers/public/findByLatLong', params=pload, headers=makeHeader())
    res = Response(param.chat_id, param.type, response.json(),param)
    return res;


def error(update, context):
    """Log Errors caused by Updates."""
    logger.warning('Update "%s" caused error "%s"', update, context.error)


def select_pin(update: Update, context: CallbackContext) -> int:
    text = 'Please provide Pin Code'
    update.callback_query.answer()
    update.callback_query.edit_message_text(text=text)
    return PIN


def select_location(update: Update, context: CallbackContext) -> int:
    text = 'Please share current location'
    update.callback_query.answer()
    update.callback_query.edit_message_text(text=text)
    return LOCATION


def select_vaccinated(update: Update, context: CallbackContext) -> int:
    text = 'Thanks for using me !!!'
    update.callback_query.answer()
    update.callback_query.edit_message_text(text=text)
    return END


def trigger():
    """Start the bot."""
    # Create the Updater and pass it your bot's token.
    # Make sure to set use_context=True to use the new context based callbacks
    # Post version 12 this will no longer be necessary
    updater = Updater(DefaultConfig.TOKEN)

    polling_executor = concurrent.futures.ThreadPoolExecutor(max_workers=5)
    # Get the dispatcher to register handlers
    dp = updater.dispatcher
    dp.user_data['polling_executor'] = polling_executor
    # on noncommand i.e message - echo the message on Telegram
    # dp.add_handler(MessageHandler(Filters.text, echo))
    # params= VaccineHandler.Params
    
    conv_handler = ConversationHandler(
        entry_points=[CommandHandler('start', start)],
        states={
        SELECTING_ACTION:
                     [CallbackQueryHandler(select_pin, pattern='^' + str(PIN) + '$'),
                      CallbackQueryHandler(select_location, pattern='^' + str(LOCATION) + '$'),
                      CallbackQueryHandler(select_vaccinated, pattern='^' + str(END) + '$')
                      ],
        PIN: [MessageHandler(Filters.regex("^[0-9]{6}$"), pin)],
        LOCATION: [MessageHandler(Filters.location, location), CommandHandler('vaccinated', vaccinated)],
        SELECT_DATE: [CallbackQueryHandler(select_date)],
        DATE: [MessageHandler(Filters.regex("y|Y|n|N"),date_complete),CommandHandler('vaccinated', vaccinated)],
        SELECT_DOSE: [CallbackQueryHandler(select_dose)],
        DOSE: [MessageHandler(Filters.text,dose),CommandHandler('vaccinated', vaccinated)],
        },
        fallbacks=[CommandHandler('cancel', cancel)],
      )
    rth = run_threaded(lambda:sendMessage(), "response")
    # pth.start()
    rth.start()
    # log all errors
    dp.add_handler(conv_handler)
    dp.add_error_handler(error)
    # Start the Bot
    if DefaultConfig.MODE == 'webhook':
        updater.start_webhook(listen="0.0.0.0",
                              port=int(DefaultConfig.PORT),
                              url_path=DefaultConfig.TOKEN,
                              webhook_url=DefaultConfig.URL + DefaultConfig.TOKEN)
        #updater.bot.setWebhook(DefaultConfig.URL + DefaultConfig.TOKEN)
    else:
    # Start the Bot
        updater.start_polling()
        updater.idle()
    # Run the bot until you press Ctrl-C or the process receives SIGINT,
    # SIGTERM or SIGABRT. This should be used most of the time, since
    # start_polling() is non-blocking and will stop the bot gracefully.
   # 


@application.route('/', methods=['GET', 'HEAD'])
def index():
    # if not cache['started']:
    #    trigger()
    #    cache['started'] = True
    return 'Welcome'

@application.route('/vaccine/', methods=['GET', 'HEAD'])
def vaccine_get():
    if not cache['started']:
       trigger()
       cache['started'] = True
    return 'Trigger started'


class DefaultConfig:
    MODE = os.environ.get("MODE", "webhook")
    LOG_LEVEL = os.environ.get('LOG_LEVEL', 'INFO').upper()
    PORT = os.environ.get("LISTENPORT", "3298")
    TOKEN=os.environ.get("TOKEN","")
    URL=os.environ.get("URL","")
    
    @staticmethod
    def init_logging():
        logging.basicConfig(format='%(asctime)s - %(levelname)s - %(message)s',
                            level=DefaultConfig.LOG_LEVEL)
        #logging.config.fileConfig('logging.conf')
if __name__ == '__main__':
    DefaultConfig.init_logging()
    if DefaultConfig.MODE == 'webhook':
        cache['started'] = False
        application.run()
    else:
        cache['started'] = True;
        trigger()
