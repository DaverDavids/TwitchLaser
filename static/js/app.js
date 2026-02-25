// TwitchLaser Control Panel JavaScript

let statusInterval;
let queueInterval;
let placementInterval;

// Initialize on page load
document.addEventListener('DOMContentLoaded', function() {
    console.log('TwitchLaser Control Panel loaded');

    // Load initial config
    loadConfig();

    // Start status updates
    updateStatus();
    statusInterval = setInterval(updateStatus, 2000);

    // Start queue updates
    updateQueue();
    queueInterval = setInterval(updateQueue, 3000);

    // Start placement updates
    updatePlacements();
    placementInterval = setInterval(updatePlacements, 5000);
});

// Update system status
async function updateStatus() {
    try {
        const response = await fetch('/api/status');
        const data = await response.json();

        // Update LEDs
        document.getElementById('laser-led').className = 'led ' + (data.laser_connected ? 'on' : 'off');
        document.getElementById('twitch-led').className = 'led ' + (data.twitch_running ? 'on' : 'off');
        document.getElementById('camera-led').className = 'led ' + (data.camera_running ? 'on' : 'off');

        // Update status text
        document.getElementById('laser-status').textContent = data.laser_connected ? 'Connected' : 'Disconnected';
        document.getElementById('twitch-status').textContent = data.twitch_running ? 'Running' : 'Stopped';
        document.getElementById('camera-status').textContent = data.camera_running ? 'Running' : 'Stopped';

        // Update stats
        document.getElementById('queue-size').textContent = data.queue_size;
        document.getElementById('placement-count').textContent = data.placements;
        document.getElementById('coverage').textContent = data.coverage.toFixed(1) + '%';

        // Update Twitch button
        const twitchBtn = document.getElementById('twitch-toggle');
        twitchBtn.textContent = data.twitch_running ? 'Stop Monitoring' : 'Start Monitoring';

    } catch (error) {
        console.error('Status update error:', error);
    }
}

// Load configuration
async function loadConfig() {
    try {
        const response = await fetch('/api/config');
        const config = await response.json();

        // Populate form fields
        document.getElementById('laser-power').value = config.laser_settings.power_percent;
        document.getElementById('laser-speed').value = config.laser_settings.speed_mm_per_min;
        document.getElementById('laser-passes').value = config.laser_settings.passes;
        document.getElementById('text-height').value = config.text_settings.initial_height_mm;
        document.getElementById('min-height').value = config.text_settings.min_height_mm;
        if (config.text_settings.font) document.getElementById('font-select').value = config.text_settings.font;
		document.getElementById('area-width').value    = config.engraving_area.active_width_mm;
		document.getElementById('area-height').value   = config.engraving_area.active_height_mm;
		if (config.laser_settings.spindle_max)
			document.getElementById('spindle-max').value = config.laser_settings.spindle_max;

    } catch (error) {
        console.error('Config load error:', error);
    }
}

// Save settings
async function saveSettings() {
    const updates = {
        laser_settings: {
            power_percent: parseInt(document.getElementById('laser-power').value),
            speed_mm_per_min: parseInt(document.getElementById('laser-speed').value),
            passes: parseInt(document.getElementById('laser-passes').value)
        },
        text_settings: {
            initial_height_mm: parseFloat(document.getElementById('text-height').value),
            min_height_mm: parseFloat(document.getElementById('min-height').value),
            font: document.getElementById('font-select').value
        },
		text_settings: {
			initial_height_mm: parseFloat(document.getElementById('text-height').value),
			min_height_mm:     parseFloat(document.getElementById('min-height').value),
			font:              document.getElementById('font-select').value
		},
		laser_settings: {
			power_percent:    parseInt(document.getElementById('laser-power').value),
			speed_mm_per_min: parseInt(document.getElementById('laser-speed').value),
			passes:           parseInt(document.getElementById('laser-passes').value),
			spindle_max:      parseInt(document.getElementById('spindle-max').value)
		}
		// Note: work area is saved separately via /api/work_area, not /api/config
    };

    try {
        const response = await fetch('/api/config', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify(updates)
        });

        const result = await response.json();
        showMessage('Settings saved successfully', 'success');

    } catch (error) {
        showMessage('Failed to save settings', 'error');
        console.error('Save settings error:', error);
    }
}

// Test engraving
async function testEngrave() {
    const text = document.getElementById('test-text').value.trim();

    if (!text) {
        showMessage('Please enter text to engrave', 'error', 'test-result');
        return;
    }

    try {
        const response = await fetch('/api/test_engrave', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({text: text})
        });

        const result = await response.json();

        if (result.success) {
            showMessage(result.message, 'success', 'test-result');
            document.getElementById('test-text').value = '';
        } else {
            showMessage(result.message, 'error', 'test-result');
        }

    } catch (error) {
        showMessage('Test engrave failed', 'error', 'test-result');
        console.error('Test engrave error:', error);
    }
}

// Laser control functions
async function laserHome() {
    try {
        const response = await fetch('/api/laser_home', {method: 'POST'});
        const result = await response.json();
        showMessage('Homing laser...', 'success', 'command-result');
    } catch (error) {
        showMessage('Home command failed', 'error', 'command-result');
    }
}

async function laserUnlock() {
    try {
        const response = await fetch('/api/laser_unlock', {method: 'POST'});
        const result = await response.json();
        showMessage('Laser unlocked', 'success', 'command-result');
    } catch (error) {
        showMessage('Unlock command failed', 'error', 'command-result');
    }
}

async function laserStop() {
    if (!confirm('Emergency stop the laser?')) return;

    try {
        const response = await fetch('/api/laser_stop', {method: 'POST'});
        const result = await response.json();
        showMessage('LASER STOPPED', 'success', 'command-result');
    } catch (error) {
        showMessage('Stop command failed', 'error', 'command-result');
    }
}

async function sendCommand() {
    const command = document.getElementById('gcode-command').value.trim();

    if (!command) {
        showMessage('Enter a command', 'error', 'command-result');
        return;
    }

    try {
        const response = await fetch('/api/laser_command', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({command: command})
        });

        const result = await response.json();

        if (result.success) {
            showMessage('Response: ' + result.response, 'success', 'command-result');
        } else {
            showMessage('Command failed', 'error', 'command-result');
        }

    } catch (error) {
        showMessage('Command failed', 'error', 'command-result');
    }
}

// Toggle Twitch monitoring
async function toggleTwitch() {
    try {
        const response = await fetch('/api/twitch_toggle', {method: 'POST'});
        const result = await response.json();
        updateStatus();
    } catch (error) {
        console.error('Twitch toggle error:', error);
    }
}

// Clear all placements
async function clearPlacements() {
    if (!confirm('Clear all placement data? This cannot be undone.')) return;

    try {
        const response = await fetch('/api/clear_placements', {method: 'POST'});
        const result = await response.json();
        showMessage('All placements cleared', 'success');
        updatePlacements();
    } catch (error) {
        showMessage('Failed to clear placements', 'error');
    }
}

// Update queue display
async function updateQueue() {
    try {
        const response = await fetch('/api/queue');
        const data = await response.json();

        const queueList = document.getElementById('queue-list');

        if (data.queue.length === 0) {
            queueList.innerHTML = '<p style="color: #95a5a6;">Queue empty</p>';
        } else {
            queueList.innerHTML = data.queue.map(item => 
                `<div class="queue-item">${item.name} <small>(${item.source})</small></div>`
            ).join('');
        }

    } catch (error) {
        console.error('Queue update error:', error);
    }
}

// Update placement visualization
async function updatePlacements() {
    try {
        const response = await fetch('/api/placements');
        const data = await response.json();

        const canvas = document.getElementById('placement-canvas');
        const ctx = canvas.getContext('2d');

        // Clear canvas
        ctx.fillStyle = '#16213e';
        ctx.fillRect(0, 0, canvas.width, canvas.height);

        // Scale to fit canvas
		const scaleX = canvas.width  / (data.active_width  || data.machine_width  || 200);
		const scaleY = canvas.height / (data.active_height || data.machine_height || 298);

        // Draw placements
        data.placements.forEach((p, i) => {
            const x = p.x * scaleX;
            const y = p.y * scaleY;
            const w = p.width * scaleX;
            const h = p.height * scaleY;

            // Draw rectangle
            ctx.fillStyle = 'rgba(155, 89, 182, 0.5)';
            ctx.fillRect(x, y, w, h);

            ctx.strokeStyle = '#9b59b6';
            ctx.strokeRect(x, y, w, h);

            // Draw text
            ctx.fillStyle = '#ecf0f1';
            ctx.font = '10px sans-serif';
            ctx.fillText(p.name, x + 2, y + h/2);
        });

    } catch (error) {
        console.error('Placement update error:', error);
    }
}

// Show message helper
function showMessage(text, type, elementId = 'test-result') {
    const element = document.getElementById(elementId);
    element.textContent = text;
    element.className = 'message ' + type;
    element.style.display = 'block';

    setTimeout(() => {
        element.style.display = 'none';
    }, 5000);
}
