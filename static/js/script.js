// Inside script.js
function editReviewPeriod() {
    let startSpan = document.getElementById('review-start');
    let endSpan = document.getElementById('review-end');
    
    // Parsing the UK format back to YYYY-MM-DD for the date picker values
    let startDate = new Date(startSpan.innerText.split('/').reverse().join('-'));
    let endDate = new Date(endSpan.innerText.split('/').reverse().join('-'));

    // Replace the text with input fields and set value as YYYY-MM-DD (date input format)
    startSpan.innerHTML = `<input type="date" id="new-review-start" value="${startDate.toISOString().split('T')[0]}">`;
    endSpan.innerHTML = `<input type="date" id="new-review-end" value="${endDate.toISOString().split('T')[0]}">`;

    // Change the button text to 'Save'
    document.querySelector('.edit-button').innerText = 'Save';
    document.querySelector('.edit-button').onclick = saveReviewPeriod;
}

function saveReviewPeriod() {
    let newStartDate = document.getElementById('new-review-start').value;
    let newEndDate = document.getElementById('new-review-end').value;

    // Convert the YYYY-MM-DD date picker values to UK format (dd/mm/yyyy)
    let formattedStartDate = new Date(newStartDate).toLocaleDateString('en-GB');
    let formattedEndDate = new Date(newEndDate).toLocaleDateString('en-GB');

    // AJAX call to send data to backend for saving
    fetch('/save_review_period', {
        method: 'POST',
        headers: {
            'Content-Type': 'application/x-www-form-urlencoded',
        },
        body: new URLSearchParams({
            'start_date': newStartDate,
            'end_date': newEndDate
        })
    }).then(response => {
        if (response.ok) {
            // Update the display with new dates in dd/mm/yyyy format
            document.getElementById('review-start').innerText = formattedStartDate;
            document.getElementById('review-end').innerText = formattedEndDate;

            console.log("Review period saved successfully.");

            // Fetch and update the review period statistics
            fetch(`/get_review_period_stats?start_date=${newStartDate}&end_date=${newEndDate}`)
                .then(response => response.json())
                .then(data => {
                    console.log("Fetched review period stats:", data);
                    
                    // Update DOM with fetched stats
                    document.querySelector('.stat-box p[data-type="items_ordered"]').innerText = data.items_ordered_review_period;
                    document.querySelector('.stat-box p[data-type="items_reviewed"]').innerText = data.items_reviewed_review_period;
                    let reviewPercentageElement = document.querySelector('.stat-box p[data-type="review_percentage"]');
                    reviewPercentageElement.innerText = `${data.review_percentage}%`;
                    reviewPercentageElement.style.color = data.review_percentage_color;
                })
                .catch(error => {
                    console.error("Error fetching updated stats:", error);
                });

            // Revert button text back to 'Edit'
            document.querySelector('.edit-button').innerText = 'Edit';
            document.querySelector('.edit-button').onclick = editReviewPeriod;
        } else {
            alert('Failed to save review period.');
        }
    }).catch(error => {
        console.error("Error saving review period:", error);
    });
}

// Define the toggleDarkMode function globally
function toggleDarkMode() {
    const body = document.body; // Target the body element
    const pageWrapper = document.querySelector('.page-wrapper'); // Target the main wrapper

    if (!pageWrapper) {
        console.error('Page wrapper element not found.');
        return;
    }

    const checkbox = document.getElementById('dark-mode-checkbox');
    const isDarkMode = checkbox.checked;

    // Toggle the dark-mode class on both the body and page wrapper
    if (isDarkMode) {
        body.classList.add('dark-mode'); // Add to body for outer background
        pageWrapper.classList.add('dark-mode'); // Add to page wrapper for inner content
    } else {
        body.classList.remove('dark-mode');
        pageWrapper.classList.remove('dark-mode');
    }

    // Save dark mode preference to localStorage
    console.log(`Setting darkMode to: ${isDarkMode ? 'enabled' : 'disabled'}`);
    localStorage.setItem('darkMode', isDarkMode ? 'enabled' : 'disabled');
}

// Make sure all the code runs after the DOM is loaded
document.addEventListener('DOMContentLoaded', function () {
    const checkbox = document.getElementById('dark-mode-checkbox');
    const body = document.body;
    const pageWrapper = document.querySelector('.page-wrapper');
    
    // Load the dark mode preference from localStorage
    const darkModeSetting = localStorage.getItem('darkMode');
    console.log(`Dark mode preference loaded: ${darkModeSetting}`);

    if (darkModeSetting === 'enabled') {
        checkbox.checked = true;
        body.classList.add('dark-mode'); // Apply to body for outer background
        pageWrapper.classList.add('dark-mode'); // Apply to page wrapper for inner content
    }

    // Attach the toggleDarkMode function to the checkbox
    checkbox.addEventListener('change', toggleDarkMode);

    // Apply dark mode on page load based on localStorage preference
    (function applyDarkModeOnLoad() {
        console.log(`Applying dark mode on load, current setting: ${darkModeSetting}`);
        if (darkModeSetting === 'enabled') {
            if (body) {
                body.classList.add('dark-mode');
            }
            if (pageWrapper) {
                pageWrapper.classList.add('dark-mode');
            }
        }
    })();
});

document.addEventListener('DOMContentLoaded', function() {
    updateItemCount();

    // Fetch item count based on the current filter
    function updateItemCount() {
        fetch(window.location.href)
            .then(response => response.json())
            .then(data => {
                document.getElementById('item-count').textContent = `Total Items: ${data.item_count}`;
            })
            .catch(error => {
                console.error("Error fetching item count:", error);
            });
    }
});
