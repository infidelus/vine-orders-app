# vine-orders-app
A database to track Amazon Vine Orders

This Flask application was written almost entirely by ChatGPT.  Other than fixing obvious mistakes I noticed it make my only input was telling it what I wanted, liked and didn't like, a little bit of CSS and HTML, and a lot of beta testing and banging my head on the table because, while ChatGPT is actually quite clever it's also exceptionally stupid sometimes and has the memory retention of a fish!

Other than the obvious bits you can see in the application, there is also a hidden import function as detailed in the import_csv route in app.py which requires a VineOrders.csv file to be in the root folder.  Additionally there is a Python script to import prices.  This was separate because when I started playing with the program I only had the Amazon data that I requested which, as Vine items are free, all show £0.00.  I was considering manually entering all the prices, and started to do so.  I then got exceptionally bored and, as I had another CSV file that I had been using up to that point that contained most of the prices, decided to ask ChatGPT to write me a script to match the URLS and import the relevant prices.

The application works quite well with the only notable issue being scraping the price on certain items, but this is likely because Amazon don't like it when we don't pay them to scrape product information from their site, and I think ChatGPT did rather a good job of the overall site in the end even if it had me swearing quite regularly during the whole process.

When I decided see if ChatGPT could make a working application I also decided I wanted it running as a Docker container on my Unraid server so it was available 24/7/365 from whatever device I happened to be using.  It does not need to be a Docker, but you do need to have the dependencies listed in requirements.txt installed if you want to run it locally.

A few images of the app:

![dashboard](https://github.com/user-attachments/assets/88b4c4f3-c5b0-4527-b8e9-9987630aafc0)
![orders](https://github.com/user-attachments/assets/28b4ea89-35be-42f0-873c-432cdd78d2bf)
![edit-order](https://github.com/user-attachments/assets/90c351dc-fad7-4c16-83f2-62eb98c3261e)

I should also mention and say thanks to Reddit user AussieA1 who shared the code from the Python tkinter app he created himself that gave me the idea for this app, and which helped me get the Find button working to scrape the description and price from the Amazon website.

## Installation

This is a Python project so you will need to install Python if it is not already installed.  I used 3.12 for my Docker setup but any recent version should work.  See [Python.org](https://www.python.org/downloads/).  Linux users should already have Python installed so can move straight to the next step.

To install the dependencies in requirements.txt you will need pip.  Please see the [pip documentation](https://pip.pypa.io/en/stable/installation/).

If you want to run this script as a Docker container you will also need to install Docker.  Instructions can be found on the [dockerdocs website](https://docs.docker.com/engine/install/).

Once installed you simply need to run app.py from the command line / terminal.  This will either be in the form of 'python app.py' or 'python3 app.py', depending on your Operating System.  Once you have it working you can add a desktop shortcut to launch it if you are not running it 24/7 in a Docker or other web server.

If you are running the app on your local machine then it will be available in your web browser at http://127.0.0.1:5000/.  Substitute the IP address if you are running the app on a different machine.  The port number can also be changed by editing app.py.  At the very bottom of the script you will see the line:

> app.run(host='0.0.0.0', port=5000)

Change the port number as required, save the file and then restart the app and adjust the browser URL port.

## Import Vine Orders

There is also now a button on the index page that allows you to import Vine orders.  This should only import items where the URL does not already exist in the database.  First, download your data from Amazon.  There should be a VineOrders.csv file, or something similar (rename it to VineOrders.csv if it has a different filename) which would have columns for url, date_ordered and description.  Move this file to the root folder of the app (where app.py is) then press the 'Import CSV' button.  If everything is in place you should see a popup message either confirming the data has been imported, including any new records that do not exist in the database, or that VineOrders.csv is missing from the root folder.
