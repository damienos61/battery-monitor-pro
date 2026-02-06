#  Battery Monitor Pro

**Battery Monitor Pro** is a lightweight Windows utility to monitor your battery status and receive smart alerts. It also provides automatic actions such as hibernation or activating Power Saver mode.

---

##  Features

* Real-time monitoring of battery level and remaining time
* Alerts for **critical** or **full** battery with notifications and sound
* Simple charge history to track variations
* Automatic **Power Saver** mode activation (Windows)
* Configurable automatic hibernation
* Custom command execution on critical battery level
* System tray icon with quick action menu
* Persistent configuration

---

##  Installation

1. Clone the project:

```bash
git clone https://github.com/yourusername/battery-monitor-pro.git
cd battery-monitor-pro
```

2. Install the recommended dependencies:

```bash
pip pip install -r requirements.txt
```

3. Run the application:

```bash
python battery_monitor_pro_windows.py
```

> On Windows, it is recommended to create a `.exe` with PyInstaller for more reliable notifications:

```bash
pyinstaller --onefile --windowed battery_monitor_pro_windows.py
```

---

##  Usage

* Adjust critical and full battery thresholds directly in the interface
* Enable or disable notifications and sounds as you prefer
* Use the system tray menu for quick access to actions like Power Saver or Hibernation

---

##  Structure

```
battery-monitor-pro/
├─ battery_monitor_pro_windows.py   # Main script
├─ config.json                     # User configuration
├─ monitor.log                     # Log file
├─ requirements.txt                # Python dependencies
└─ README.md                       # Documentation
```

---

##  Notes

* All critical actions require confirmation to prevent mistakes
* Only compatible with **Windows** for advanced system features
* Lightweight and ready to use on any modern PC

**Enjoy and take care of your battery! 🔋**
