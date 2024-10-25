# vine-orders-app
A database to track Amazon Vine Orders

This Flask application was written almost entirely by ChatGPT.  Other than fixing obvious mistakes I noticed it make my only input was telling it what I wanted, liked and didn't like, a little bit of CSS and HTML, and a lot of beta testing and banging my head on the table because, while ChatGPT is actually quite clever it's also exceptionally stupid sometimes and has the memory retention of a fish!

Other than the obvious bits you can see in the application, there is also a hidden import function as detailed in the import_csv route in app.py which requires a VineOrders.csv file to be in the root folder.  Additionally there is a Python script to import prices.  This was separate because when I started playing with the program I only had the Amazon data that I requested which, as Vine items are free, all show £0.00.  I was considering manually entering all the prices, and started to do so.  I then got exceptionally bored and, as I had another CSV file that I had been using up to that point that contained most of the prices, decided to ask ChatGPT to write me a script to match the URLS and import the relevant prices.

The application works quite well with the only notable issue being scraping the price on certain items, but this is likely because Amazon don't like it when we don't pay them to scrape product information from their site, and I think ChatGPT did rather a good job of the overall site in the end even if it had me swearing quite regularly during the whole process.

When I decided see if ChatGPT could make a working application I also decided I wanted it running as a Docker container on my Unraid server so it was available 24/7/365 from whatever device I happened to be using.  It does not need to be a Docker, but you do need to have the dependencies listed in requirements.txt installed if you want to run it locally.

A few images of the app:

![dashboard](https://github.com/user-attachments/assets/1d04ece4-14ce-43c4-9d36-ddec76cad04c)

![orders](https://github.com/user-attachments/assets/83e23bad-009c-49ea-ab23-1dba09cb3bd3)

![edit-order](https://github.com/user-attachments/assets/90c351dc-fad7-4c16-83f2-62eb98c3261e)

I should also mention and say thanks to Reddit user AussieA1 who shared the code from the Python tkinter app he created himself that gave me the idea for this app, and which helped me get the Find button working to scrape the description and price from the Amazon website.
