from config import config, statOrderBody, helperBody, ephemeralBody, rateToScore
import requests
import time, datetime
from apscheduler.schedulers.background import BackgroundScheduler
import json
import logging
import sqlite3
import os
from flask import g


channel_jobs_dict = {}
channel_food_order_count_dict = {}
channel_user_food_rate_dict = {}

DATABASE = "rate.db"

def get_db():
    db = getattr(g, '_database', None)
    db_is_new = not os.path.exists(DATABASE)
    if db is None:
        db = g._database = sqlite3.connect(DATABASE)
    if db_is_new:        
        with open("schema.sql", 'rt') as f:
            schema = f.read()
        db.executescript(schema)
    return db

def handlePayload(req):
    global channel_food_order_count_dict
    global channel_user_food_rate_dict

    if 'payload' not in req:
        logging.warning("invalid params for payload")
        return "Invalid Params"
    payload = json.loads(req['payload'])
    action = payload['actions'][0]['value']
    channel = payload['channel']['id']
    callback_id = int(payload['original_message']['attachments'][0]['callback_id'])
    user = payload['user']['id']

    if callback_id < channel_food_order_count_dict[channel]:
        res = "This is an out of date food order rate."
    elif callback_id > channel_food_order_count_dict[channel]:
        logging.warn("callback_id > channel_food_order_count_dict[channel]")
        print channel_food_order_count_dict[channel], callback_id, callback_id == channel_food_order_count_dict[channel]
        res = "callback_id is newer than current food order, will check."
    else:
        # Users rate multiple times.
        if user not in channel_user_food_rate_dict[channel]:
            # It's a valid rate
            saveRate(channel, user, action, callback_id)
            channel_user_food_rate_dict[channel][user] = rateToScore[action]
            res = "Successful, thank you for your rating."
        else:
            # It's an invalid rate
            res = "You have already rated before, thanks."
    return res


def saveRate(channel, user, action, callback_id):
    db = get_db()
    value = user + '\',' + str(callback_id) + ',' + str(rateToScore[action])
    db.execute("insert into rate (user, count, score) values ('"+value+")")    
    db.commit()


def handleJson(req):
    global channel_food_order_count_dict
    if 'challenge' in req:
        return handleChallenge(req)
    elif 'event' in req:
        channel = req['event']['channel']
        user = req['event']['user']
        if channel not in config:
            logging.warn("invalid channel source")
            return "Invalid Channel Source"
        params = req['event']['text'].split(' ')
        if len(params) < 2:
            logging.warning("invalid params for event")
            return "Invalid Params"

        elif params[1].startswith('tom'):
            if postOrder(params, channel, 'tomorrow'):
                scheduleJob(channel, user, 'tomorrow')
            else:
                logging.warning("invalid params for tomorrow")
                return "Invalid Params"
        elif params[1].startswith('tod'):
            if postOrder(params, channel, 'today'):
                scheduleJob(channel, user, 'today')
            else:
                logging.warning("invalid params for today")
                return "Invalid Params"  

        elif params[1] == 'stat':
            statOrder(channel, callback_id=1)
        elif params[1] == 'help':
            helperDoc(channel)
        elif params[1] == 'status':
            showStatus(channel)
        elif params[1] == 'clear':
            clearJobs(channel)
        else:
            logging.warning("invalid params for actions")
            send(channel, {"text":"Cannot understand your command..."})
            return "Invalid Params"
        res = "OK"
    else:
        res = "OMG"
    return res

def clearJobs(channel):
    global channel_jobs_dict

    if channel not in channel_jobs_dict:
        logging.warn("No record for this channel"+channel)
        send(channel, {"text":"No record for this channel."})
    else:
        for job in channel_jobs_dict[channel]:
            logging.debug("job removed: " + job)
            job.remove()
        channel_jobs_dict[channel] = []
        logging.info("Scheduled jobs have been cleared.")
        send(channel, {"text":"Scheduled jobs have been cleared."})

def showStatus(channel):
    global channel_jobs_dict
    global channel_food_order_count_dict

    if channel not in channel_jobs_dict:
        logging.warn("No record for this channel.")
        send(channel, {"text":"No record for this channel."})
    else:
        logging.debug("This channel has "+str(len(channel_jobs_dict[channel])/3)+" scheduled jobs.")
        send(channel, {"text":"This channel has "+str(len(channel_jobs_dict[channel])/3)+" scheduled jobs."})

def checkUserOfEvent(event, id):
    if 'user' not in event:
        return False
    if event['user'] != id:
        return False
    return True

def statOrder(channel, callback_id):
    # init channel_user_food_rate_dict if needed
    global channel_user_food_rate_dict
    if channel not in channel_user_food_rate_dict:
        channel_user_food_rate_dict[channel] = {}    

    send(channel, statOrderBody(callback_id))

def helperDoc(channel):
    send(channel, helperBody)

def postOrder(params, channel, date_str):
    if len(params) < 3:
        return False
    url = params[2]
    if len(url) == 0:
        return False
    name = params[3] if (len(params)==4) else "Demo"
    body = '<!here> ' + url + '\nFor '+ date_str + '\'s ' + name + ' meeting\'s order\n'
    body += 'Order will be closed at 11:00AM tomorrow\nThanks!'
    body = {"text":body}
    send(channel, body)
    return True

def scheduleJob(channel, user, date_str):

    # Food order count for current channel increment
    global channel_food_order_count_dict
    if channel not in channel_food_order_count_dict:
        channel_food_order_count_dict[channel] = 1
    else:
        channel_food_order_count_dict[channel] = channel_food_order_count_dict[channel] + 1    

    # init channel_user_food_rate_dict if needed
    if channel not in channel_user_food_rate_dict:
        channel_user_food_rate_dict[channel] = {}

    # Prepare scheduleJob
    scheduler = BackgroundScheduler()

    remind_date = datetime.date.today()
    if date_str == 'tomorrow':
        remind_date += datetime.timedelta(days=1)

    global channel_jobs_dict
    if channel not in channel_jobs_dict:
        channel_jobs_dict[channel] = []
    
    job1 = scheduler.add_job(lambda: sendAlert_10min(channel), 'cron', year=remind_date.year, month=remind_date.month, 
            day=remind_date.day, hour=10, minute=50)    
    job2 = scheduler.add_job(lambda: closeAlert(channel, user), 'cron', year=remind_date.year, month=remind_date.month, 
            day=remind_date.day, hour=11, minute=00)
    job3 = scheduler.add_job(lambda: statOrder(channel, channel_food_order_count_dict[channel]), 'cron', year=remind_date.year, month=remind_date.month, 
            day=remind_date.day, hour=13, minute=10)

    channel_jobs_dict[channel].append(job1)
    channel_jobs_dict[channel].append(job2) 
    channel_jobs_dict[channel].append(job3)
         
    scheduler.start()

def sendAlert_1hour(channel):
    body = {"text":"<!here> food order will be closed in 1 hour."}
    send(channel, body)

def sendAlert_10min(channel):
    body = {"text":"<!here> food order will be closed in 10 min."}
    send(channel, body)

def closeAlert(channel, user):
    body = {"text":"<@"+user+"> closing order, please"}
    send(channel, body)

def send(channel, body):
    url = config[channel]
    requests.post(url, json=body)

def handleChallenge(req):
    return req['challenge']
