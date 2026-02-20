// Debug script to check what's happening
console.log('=== DEBUG START ===');
console.log('Document ready state:', document.readyState);
console.log('CONFIG defined:', typeof CONFIG !== 'undefined');

// Check if main elements exist
setTimeout(() => {
    console.log('=== CHECKING ELEMENTS ===');
    console.log('sidebar-toggle:', document.getElementById('sidebar-toggle'));
    console.log('refresh-all:', document.getElementById('refresh-all'));
    console.log('nav-items:', document.querySelectorAll('.nav-item').length);
    
    // Check if functions are defined
    console.log('=== CHECKING FUNCTIONS ===');
    console.log('init defined:', typeof init !== 'undefined');
    console.log('setupNavigation defined:', typeof setupNavigation !== 'undefined');
    console.log('pollAll defined:', typeof pollAll !== 'undefined');
    
    // Check if event listeners are attached
    const refreshBtn = document.getElementById('refresh-all');
    if (refreshBtn) {
        console.log('refresh-all listeners:', getEventListeners(refreshBtn));
    }
}, 1000);

// Listen for DOMContentLoaded
document.addEventListener('DOMContentLoaded', () => {
    console.log('=== DOMContentLoaded FIRED ===');
});

// Listen for load
window.addEventListener('load', () => {
    console.log('=== WINDOW LOAD FIRED ===');
});

console.log('=== DEBUG END ===');
