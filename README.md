# vine-orders-app
A database to track Amazon Vine Orders

This Flask application was written almost entirely by ChatGPT.  Other than fixing obvious mistakes I noticed it make my only input was telling it what I wanted, liked and didn't like, a little bit of CSS and HTML, and a lot of beta testing and banging my head on the table because while ChatGPT is actually quite clever it's also exceptionally stupid sometimes and has the memory retention of a fish!

Other than the obvious bits you can see in the application, there is also a hidden import function as detailed in the import_csv route in app.py which requires a VineOrders.csv file to be in the root folder.  Additionally there is a python file to import prices.  This was separate because when I started playing with the program I only had the Amazon data that I requested which, as Vine items are free, all show £0.00.  I was considering manually entering all the prices, and started to do so.  I then got exceptionally bored and, as I had another CSV file with most of the prices in, decided to ask ChatGPT to write me a script to match the URLS and import the relevant prices.

The appication works very well with no obvious errors left and I think ChatGPT did rather a good job in the end, even if it had me swearing quite regularly during the whole process.
