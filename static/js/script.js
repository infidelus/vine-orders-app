// Inside script.js

// Function to handle editing and saving the review period
function editReviewPeriod() {
    let startSpan = document.getElementById('review-start');
    let endSpan = document.getElementById('review-end');

    let startDate = new Date(startSpan.innerText.split('/').reverse().join('-'));
    let endDate = new Date(endSpan.innerText.split('/').reverse().join('-'));

    startSpan.innerHTML = `<input type="date" id="new-review-start" value="${startDate.toISOString().split('T')[0]}">`;
    endSpan.innerHTML = `<input type="date" id="new-review-end" value="${endDate.toISOString().split('T')[0]}">`;

    document.querySelector('.edit-button').innerText = 'Save';
    document.querySelector('.edit-button').onclick = saveReviewPeriod;
}

function saveReviewPeriod() {
    let newStartDate = document.getElementById('new-review-start').value;
    let newEndDate = document.getElementById('new-review-end').value;

    let formattedStartDate = new Date(newStartDate).toLocaleDateString('en-GB');
    let formattedEndDate = new Date(newEndDate).toLocaleDateString('en-GB');

    fetch('/save_review_period', {
        method: 'POST',
        headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
        body: new URLSearchParams({ 'start_date': newStartDate, 'end_date': newEndDate })
    }).then(response => {
        if (response.ok) {
            document.getElementById('review-start').innerText = formattedStartDate;
            document.getElementById('review-end').innerText = formattedEndDate;
            console.log("Review period saved successfully.");

            // Fetch and update the review period statistics
            fetch(`/get_review_period_stats?start_date=${newStartDate}&end_date=${newEndDate}`)
                .then(response => response.json())
                .then(data => {
                    document.querySelector('.stat-box p[data-type="items_ordered"]').innerText = data.items_ordered_review_period;
                    document.querySelector('.stat-box p[data-type="items_reviewed"]').innerText = data.items_reviewed_review_period;
                    let reviewPercentageElement = document.querySelector('.stat-box p[data-type="review_percentage"]');
                    reviewPercentageElement.innerText = `${data.review_percentage}%`;
                    reviewPercentageElement.style.color = data.review_percentage_color;
                })
                .catch(error => console.error("Error fetching updated stats:", error));

            document.querySelector('.edit-button').innerText = 'Edit';
            document.querySelector('.edit-button').onclick = editReviewPeriod;
        } else {
            alert('Failed to save review period.');
        }
    }).catch(error => console.error("Error saving review period:", error));
}

// Dark mode toggle
function toggleDarkMode() {
    const body = document.body;
    const pageWrapper = document.querySelector('.page-wrapper');
    const checkbox = document.getElementById('dark-mode-checkbox');
    const isDarkMode = checkbox.checked;

    body.classList.toggle('dark-mode', isDarkMode);
    pageWrapper?.classList.toggle('dark-mode', isDarkMode);

    localStorage.setItem('darkMode', isDarkMode ? 'enabled' : 'disabled');
}

// Main DOMContentLoaded event handler
document.addEventListener('DOMContentLoaded', function () {
    const checkbox = document.getElementById('dark-mode-checkbox');
    const body = document.body;
    const pageWrapper = document.querySelector('.page-wrapper');
    const darkModeSetting = localStorage.getItem('darkMode');

    if (darkModeSetting === 'enabled') {
        checkbox.checked = true;
        body.classList.add('dark-mode');
        pageWrapper?.classList.add('dark-mode');
    }

    checkbox.addEventListener('change', toggleDarkMode);

    // Check if on the orders page before calling updateItemCount
    if (window.location.pathname.includes('/orders')) {
        updateItemCount();
    }

    // Update item count based on the current filter
    function updateItemCount() {
        fetch(`/get_item_count`)
            .then(response => response.json())
            .then(data => {
                const itemCountElement = document.getElementById('item-count');
                if (itemCountElement) {
                    itemCountElement.textContent = `Total Items: ${data.item_count}`;
                }
            })
            .catch(error => console.error("Error fetching item count:", error));
    }
});

document.getElementById('importCsvButton').addEventListener('click', function() {
    fetch('/import_csv', {
        method: 'GET'
    })
    .then(response => response.json())
    .then(data => {
        alert(data.message);
    })
    .catch(error => {
        console.error('Error:', error);
        alert('An error occurred while importing the CSV file.');
    });
});
