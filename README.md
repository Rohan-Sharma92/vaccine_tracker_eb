# Vaccine Tracker
A telegram bot for tracking vaccination centers and deployment on AWS using Elastic Beanstalk

## Installation and Deployment
### Prerequisites
1. Bot Token is already available after creating bot using BotFather
2. Installed python version is atleast 3.7 and pip is installed.

### Installation
1. Setup the following environment variables:
 TOKEN=<Telegram bot token>
 PORT=<Listen port for the web server, default is 3298>
 URL=<Https URL for setting up web hook>

2. Checkout this project and install dependencies by running following command in the project directory
	pip install -r requirements.txt
	
3. Start the bot using VaccineTracker.py script
