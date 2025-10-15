# Enhanced SlackNotifier with separate channels, daily summaries, and error categorization
import json
import socket
import requests
import threading
import time
import subprocess
from datetime import datetime, timezone
from typing import Dict, Optional, Any
from collections import defaultdict
import bittensor as bt
from utils.weight_failure_classifier import WeightFailureClassifier


class SlackNotifier:
    """Handles all Slack notifications for miners and validators with enhanced features"""

    def __init__(self, hotkey, webhook_url: Optional[str] = None, error_webhook_url: Optional[str] = None,
                 is_miner: bool = False):
        self.webhook_url = webhook_url
        self.hotkey = hotkey
        self.error_webhook_url = error_webhook_url or webhook_url  # Fallback to main if not provided
        self.enabled = bool(webhook_url)
        self.is_miner = is_miner
        self.node_type = "Miner" if is_miner else "Validator"
        self.vm_ip = self._get_vm_ip()
        self.vm_hostname = self._get_vm_hostname()
        self.git_branch = self._get_git_branch()

        # Daily summary tracking
        self.startup_time = datetime.now(timezone.utc)
        self.daily_summary_lock = threading.Lock()
        self.last_summary_date = None

        # Persistent metrics (survive restarts)
        self.metrics_file = f"{self.node_type.lower()}_lifetime_metrics.json"
        self.lifetime_metrics = self._load_lifetime_metrics()

        # Daily metrics (reset each day)
        if self.is_miner:
            self.daily_metrics = {
                "signals_processed": 0,
                "signals_failed": 0,
                "validator_response_times": [],  # All individual validator response times in ms
                "validator_counts": [],
                "trade_pair_counts": defaultdict(int),
                "successful_validators": set(),
                "error_categories": defaultdict(int),
                "failing_validators": defaultdict(int)
            }
        else:
            # Validator-specific metrics
            self.daily_metrics = {
                "weights_set_count": 0,
                "weights_set_failed": 0,
                "weights_set_times": [],  # Timestamps of when weights were set
                "no_permit_events": 0,
                "registration_failures": 0,
                "burn_uid_changes": []  # Track if burn UID changes
            }

        # Start daily summary thread
        self._start_daily_summary_thread()

    def _get_vm_ip(self) -> str:
        """Get the VM's IP address"""
        try:
            response = requests.get('https://api.ipify.org', timeout=5)
            return response.text
        except Exception as e:
            try:
                bt.logging.error(f"Got exception: {e}")
                hostname = socket.gethostname()
                return socket.gethostbyname(hostname)
            except Exception as e2:
                bt.logging.error(f"Got exception: {e2}")
                return "Unknown IP"

    def _get_vm_hostname(self) -> str:
        """Get the VM's hostname"""
        try:
            return socket.gethostname()
        except Exception as e:
            bt.logging.error(f"Got exception: {e}")
            return "Unknown Hostname"

    def _get_git_branch(self) -> str:
        """Get the current git branch"""
        try:
            result = subprocess.run(
                ['git', 'rev-parse', '--abbrev-ref', 'HEAD'],
                capture_output=True,
                text=True,
                check=True
            )
            branch = result.stdout.strip()
            if branch:
                return branch
            return "Unknown Branch"
        except Exception as e:
            bt.logging.error(f"Failed to get git branch: {e}")
            return "Unknown Branch"

    def _load_lifetime_metrics(self) -> Dict[str, Any]:
        """Load persistent metrics from file
        try:
            if os.path.exists(self.metrics_file):
                with open(self.metrics_file, 'r') as f:
                    return json.load(f)
        except Exception as e:
            bt.logging.warning(f"Failed to load lifetime metrics: {e}")
        """
        # Default metrics
        if self.is_miner:
            return {
                "total_lifetime_signals": 0,
                "total_uptime_seconds": 0,
                "last_shutdown_time": None
            }
        else:
            return {
                "total_lifetime_weights_set": 0,
                "total_uptime_seconds": 0,
                "last_shutdown_time": None
            }

    def _save_lifetime_metrics(self):
        """Save persistent metrics to file"""
        try:
            # Update uptime
            if self.lifetime_metrics.get("last_shutdown_time"):
                last_shutdown = datetime.fromisoformat(self.lifetime_metrics["last_shutdown_time"])
                downtime = (self.startup_time - last_shutdown).total_seconds()
                # Only add if downtime was reasonable (less than 7 days)
                if 0 < downtime < 7 * 24 * 3600:
                    pass  # Don't add downtime to uptime

            current_session_uptime = (datetime.now(timezone.utc) - self.startup_time).total_seconds()
            self.lifetime_metrics["total_uptime_seconds"] += current_session_uptime
            self.lifetime_metrics["last_shutdown_time"] = datetime.now(timezone.utc).isoformat()

            with open(self.metrics_file, 'w') as f:
                json.dump(self.lifetime_metrics, f)
        except Exception as e:
            bt.logging.error(f"Failed to save lifetime metrics: {e}")

    def _categorize_error(self, error_message: str) -> str:
        """Categorize error messages"""
        error_lower = error_message.lower()

        if any(keyword in error_lower for keyword in ['timeout', 'timed out', 'time out']):
            return "Timeout"
        elif any(keyword in error_lower for keyword in ['connection', 'connect', 'refused', 'unreachable']):
            return "Connection Failed"
        elif any(keyword in error_lower for keyword in ['invalid', 'decode', 'parse', 'json', 'format']):
            return "Invalid Response"
        elif any(keyword in error_lower for keyword in ['network', 'dns', 'resolve']):
            return "Network Error"
        else:
            return "Other"

    def _start_daily_summary_thread(self):
        """Start the daily summary thread"""
        if not self.enabled:
            return

        def daily_summary_loop():
            while True:
                try:
                    now = datetime.now(timezone.utc)
                    # Calculate seconds until next midnight UTC
                    next_midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
                    if next_midnight <= now:
                        next_midnight = next_midnight.replace(day=next_midnight.day + 1)

                    sleep_seconds = (next_midnight - now).total_seconds()
                    time.sleep(sleep_seconds)

                    # Send daily summary (only makes sense for miners at this moment)
                    if self.is_miner:
                        self._send_daily_summary_miner()
                    else:
                        self._send_daily_summary_validator()


                except Exception as e:
                    bt.logging.error(f"Error in daily summary thread: {e}")
                    time.sleep(3600)  # Sleep 1 hour on error

        summary_thread = threading.Thread(target=daily_summary_loop, daemon=True)
        summary_thread.start()

    def _get_uptime_str(self) -> str:
        """Get formatted uptime string"""
        current_uptime = (datetime.now(timezone.utc) - self.startup_time).total_seconds()
        total_uptime = self.lifetime_metrics["total_uptime_seconds"] + current_uptime

        if total_uptime >= 86400:
            return f"{total_uptime / 86400:.1f} days"
        else:
            return f"{total_uptime / 3600:.1f} hours"

    def _send_daily_summary_validator(self):
        """Send daily summary report for validators"""
        with self.daily_summary_lock:
            try:
                # Calculate uptime
                uptime_str = self._get_uptime_str()

                # Calculate success rate for weight setting
                total_attempts = self.daily_metrics["weights_set_count"] + self.daily_metrics["weights_set_failed"]
                if total_attempts > 0:
                    success_rate = (self.daily_metrics["weights_set_count"] / total_attempts) * 100
                else:
                    success_rate = 0.0

                # Calculate average time between weight sets
                weight_times = self.daily_metrics["weights_set_times"]
                if len(weight_times) > 1:
                    time_diffs = []
                    for i in range(1, len(weight_times)):
                        diff = (weight_times[i] - weight_times[i-1]).total_seconds() / 60  # in minutes
                        time_diffs.append(diff)
                    avg_interval = sum(time_diffs) / len(time_diffs)
                    avg_interval_str = f"{avg_interval:.1f} minutes"
                else:
                    avg_interval_str = "N/A"

                fields = [
                    {
                        "title": "📊 Daily Summary Report",
                        "value": f"Automated daily report for {datetime.now(timezone.utc).strftime('%Y-%m-%d')}",
                        "short": False
                    },
                    {
                        "title": "🕒 Validator Hotkey",
                        "value": f"...{self.hotkey[-8:]}",
                        "short": True
                    },
                    {
                        "title": "Script Uptime",
                        "value": uptime_str,
                        "short": True
                    },
                    {
                        "title": "📈 Lifetime Weights Set",
                        "value": str(self.lifetime_metrics["total_lifetime_weights_set"]),
                        "short": True
                    },
                    {
                        "title": "📅 Today's Weights Set",
                        "value": str(self.daily_metrics["weights_set_count"]),
                        "short": True
                    },
                    {
                        "title": "✅ Weight Set Success Rate",
                        "value": f"{success_rate:.1f}%",
                        "short": True
                    },
                    {
                        "title": "⏱️ Avg Interval Between Weight Sets",
                        "value": avg_interval_str,
                        "short": True
                    },
                    {
                        "title": "🖥️ System Info",
                        "value": f"Host: {self.vm_hostname}\nIP: {self.vm_ip}\nBranch: {self.git_branch}",
                        "short": True
                    }
                ]

                # Add failure details if any
                if self.daily_metrics["weights_set_failed"] > 0:
                    fields.append({
                        "title": "❌ Failed Weight Sets",
                        "value": str(self.daily_metrics["weights_set_failed"]),
                        "short": True
                    })

                if self.daily_metrics["no_permit_events"] > 0:
                    fields.append({
                        "title": "⚠️ No Permit Events",
                        "value": str(self.daily_metrics["no_permit_events"]),
                        "short": True
                    })

                if self.daily_metrics["registration_failures"] > 0:
                    fields.append({
                        "title": "🚫 Registration Failures",
                        "value": str(self.daily_metrics["registration_failures"]),
                        "short": True
                    })

                if self.daily_metrics["burn_uid_changes"]:
                    changes_str = "\n".join([
                        f"Changed from {old} to {new} at {time.strftime('%H:%M:%S')}"
                        for old, new, time in self.daily_metrics["burn_uid_changes"]
                    ])
                    fields.append({
                        "title": "🔄 Burn UID Changes",
                        "value": changes_str,
                        "short": False
                    })

                payload = {
                    "attachments": [{
                        "color": "#4CAF50",  # Green for summary
                        "fields": fields,
                        "footer": f"Taoshi {self.node_type} Daily Summary",
                        "ts": int(time.time())
                    }]
                }

                # Send to main channel (not error channel)
                response = requests.post(self.webhook_url, json=payload, timeout=10)
                response.raise_for_status()

                # Reset daily metrics after successful send
                self.daily_metrics = {
                    "weights_set_count": 0,
                    "weights_set_failed": 0,
                    "weights_set_times": [],
                    "no_permit_events": 0,
                    "registration_failures": 0,
                    "burn_uid_changes": []
                }

            except Exception as e:
                bt.logging.error(f"Failed to send daily summary: {e}")

    def _send_daily_summary_miner(self):
        """Send daily summary report"""
        with self.daily_summary_lock:
            try:
                # Calculate uptime
                uptime_str = self._get_uptime_str()

                # Validator response time stats
                response_times = self.daily_metrics["validator_response_times"]
                if response_times:
                    best_response_time = min(response_times)
                    worst_response_time = max(response_times)
                    avg_response_time = sum(response_times) / len(response_times)
                    # Calculate median
                    sorted_times = sorted(response_times)
                    n = len(sorted_times)
                    median_response_time = (sorted_times[n // 2] + sorted_times[(n - 1) // 2]) / 2
                    # Calculate 95th percentile
                    p95_index = int(0.95 * n)
                    p95_response_time = sorted_times[min(p95_index, n - 1)]
                else:
                    best_response_time = worst_response_time = avg_response_time = median_response_time = p95_response_time = 0

                # Validator count stats
                val_counts = self.daily_metrics["validator_counts"]
                if val_counts:
                    min_validators = min(val_counts)
                    max_validators = max(val_counts)
                    avg_validators = sum(val_counts) / len(val_counts)
                else:
                    min_validators = max_validators = avg_validators = 0

                # Success rate
                total_today = self.daily_metrics["signals_processed"]
                failed_today = self.daily_metrics["signals_failed"]
                success_rate = ((total_today - failed_today) / max(1, total_today)) * 100

                # Trade pair breakdown (top 10)
                trade_pairs = sorted(
                    self.daily_metrics["trade_pair_counts"].items(),
                    key=lambda x: x[1],
                    reverse=True
                )[:10]
                trade_pair_str = ", ".join([f"{pair}: {count}" for pair, count in trade_pairs]) or "None"

                # Error category breakdown
                error_categories = dict(self.daily_metrics["error_categories"])
                error_str = ", ".join([f"{cat}: {count}" for cat, count in error_categories.items()]) or "None"

                fields = [
                    {
                        "title": "📊 Daily Summary Report",
                        "value": f"Automated daily report for {datetime.now(timezone.utc).strftime('%Y-%m-%d')}",
                        "short": False
                    },
                    {
                        "title": f"🕒 {self.node_type} Hotkey",
                        "value": f"...{self.hotkey[-8:]}",
                        "short": True
                    },
                    {
                        "title": "Script Uptime",
                        "value": uptime_str,
                        "short": True
                    },
                    {
                        "title": "📈 Lifetime Signals",
                        "value": str(self.lifetime_metrics["total_lifetime_signals"]),
                        "short": True
                    },
                    {
                        "title": "📅 Today's Signals",
                        "value": str(total_today),
                        "short": True
                    },
                    {
                        "title": "✅ Success Rate",
                        "value": f"{success_rate:.1f}%",
                        "short": True
                    },
                    {
                        "title": "⚡ Validator Response Times (ms)",
                        "value": f"Best: {best_response_time:.0f}ms\nWorst: {worst_response_time:.0f}ms\nAvg: {avg_response_time:.0f}ms\nMedian: {median_response_time:.0f}ms\n95th %ile: {p95_response_time:.0f}ms",
                        "short": True
                    },
                    {
                        "title": "🔗 Validator Counts",
                        "value": f"Min: {min_validators}\nMax: {max_validators}\nAvg: {avg_validators:.1f}",
                        "short": True
                    },
                    {
                        "title": "💱 Trade Pairs",
                        "value": trade_pair_str,
                        "short": False
                    },
                    {
                        "title": "✨ Unique Validators",
                        "value": str(len(self.daily_metrics["successful_validators"])),
                        "short": True
                    },
                    {
                        "title": "🖥️ System Info",
                        "value": f"Host: {self.vm_hostname}\nIP: {self.vm_ip}\nBranch: {self.git_branch}",
                        "short": True
                    }
                ]

                if error_categories:
                    fields.append({
                        "title": "❌ Error Categories",
                        "value": error_str,
                        "short": False
                    })

                payload = {
                    "attachments": [{
                        "color": "#4CAF50",  # Green for summary
                        "fields": fields,
                        "footer": f"Taoshi {self.node_type} Daily Summary",
                        "ts": int(time.time())
                    }]
                }

                # Send to main channel (not error channel)
                response = requests.post(self.webhook_url, json=payload, timeout=10)
                response.raise_for_status()

                # Reset daily metrics after successful send
                self.daily_metrics = {
                    "signals_processed": 0,
                    "signals_failed": 0,
                    "validator_response_times": [],
                    "validator_counts": [],
                    "trade_pair_counts": defaultdict(int),
                    "successful_validators": set(),
                    "error_categories": defaultdict(int),
                    "failing_validators": defaultdict(int)
                }

            except Exception as e:
                bt.logging.error(f"Failed to send daily summary: {e}")

    def send_message(self, message: str, level: str = "info"):
        """Send a message to appropriate Slack channel based on level"""
        if not self.enabled:
            return

        try:
            # Determine which webhook to use
            if level in ["error", "warning"]:
                webhook_url = self.error_webhook_url
            else:
                webhook_url = self.webhook_url

            # Color coding for different message levels
            color_map = {
                "error": "#ff0000",
                "warning": "#ff9900",
                "success": "#00ff00",
                "info": "#0099ff"
            }

            payload = {
                "attachments": [{
                    "color": color_map.get(level, "#808080"),
                    "fields": [
                        {
                            "title": f"{self.node_type} Alert",
                            "value": message,
                            "short": False
                        },
                        {
                            "title": f"VM IP | {self.node_type} Hotkey",
                            "value": f"{self.vm_ip} | ...{self.hotkey[-8:]}",
                            "short": True
                        },
                        {
                            "title": "Script Uptime | Git Branch",
                            "value": f"{self._get_uptime_str()} | {self.git_branch}",
                            "short": True
                        }
                    ],
                    "footer": f"Taoshi {self.node_type} Notification",
                    "ts": int(time.time())
                }]
            }

            response = requests.post(webhook_url, json=payload, timeout=10)
            response.raise_for_status()

        except Exception as e:
            bt.logging.error(f"Failed to send Slack notification: {e}")

    def _classify_weight_failure(self, error_msg: str) -> str:
        """Classify weight setting failures using centralized classifier"""
        return WeightFailureClassifier.classify_failure(error_msg)

    def _should_alert_weight_failure(self, failure_type: str, consecutive_failures: int,
                                     time_since_success: float, time_since_last_alert: float) -> bool:
        """Determine if we should send a weight failure alert"""
        # Alert if we haven't had a successful weight setting in 2 hours (absolute timeout)
        if time_since_success > 7200:  # 2 hours
            return True

        # Rate limiting - but exempt critical errors and 1+ hour timeouts
        if failure_type != "critical" and time_since_success <= 3600:
            if time_since_last_alert < 600:  # 10 minutes
                return False

        # Always alert for known critical errors (no rate limiting)
        if failure_type == "critical":
            return True

        # Alert if we haven't had a successful weight setting in 1 hour
        if time_since_success > 3600:
            return True

        # Never alert for benign "too soon" errors (unless prolonged, caught above)
        if failure_type == "benign":
            return False

        # For unknown errors, alert after 2 consecutive failures
        if failure_type == "unknown" and consecutive_failures >= 2:
            return True

        return False

    def record_weight_set_success(self) -> bool:
        """Record a successful weight set for validators

        Returns:
            bool: True if a recovery alert should be sent
        """
        if not self.is_miner:
            with self.daily_summary_lock:
                # Check if we should send recovery alert
                should_send_recovery = (
                    hasattr(self, '_weight_consecutive_failures') and
                    self._weight_consecutive_failures > 0 and
                    hasattr(self, '_weight_had_critical_failure') and
                    self._weight_had_critical_failure
                )

                # Update metrics
                self.daily_metrics["weights_set_count"] += 1
                self.daily_metrics["weights_set_times"].append(datetime.now(timezone.utc))
                self.lifetime_metrics["total_lifetime_weights_set"] += 1

                # Reset failure tracking
                self._weight_consecutive_failures = 0
                self._weight_last_success_time = time.time()
                self._weight_had_critical_failure = False

                return should_send_recovery
        return False

    def record_weight_set_failure(self, error_msg: str) -> tuple[bool, str]:
        """Record a failed weight set for validators

        Args:
            error_msg: The error message from the weight setting attempt

        Returns:
            tuple: (should_alert, failure_type)
        """
        if not self.is_miner:
            with self.daily_summary_lock:
                # Initialize tracking variables if not present
                if not hasattr(self, '_weight_consecutive_failures'):
                    self._weight_consecutive_failures = 0
                if not hasattr(self, '_weight_last_success_time'):
                    self._weight_last_success_time = time.time()
                if not hasattr(self, '_weight_last_alert_time'):
                    self._weight_last_alert_time = 0
                if not hasattr(self, '_weight_had_critical_failure'):
                    self._weight_had_critical_failure = False

                # Classify the failure
                failure_type = self._classify_weight_failure(error_msg)

                # Track failure
                self._weight_consecutive_failures += 1
                if failure_type == "critical":
                    self._weight_had_critical_failure = True

                # Only increment daily metric for non-benign failures
                if failure_type != "benign":
                    self.daily_metrics["weights_set_failed"] += 1

                # Determine if we should alert
                time_since_success = time.time() - self._weight_last_success_time
                time_since_last_alert = time.time() - self._weight_last_alert_time

                should_alert = self._should_alert_weight_failure(
                    failure_type,
                    self._weight_consecutive_failures,
                    time_since_success,
                    time_since_last_alert
                )

                if should_alert:
                    self._weight_last_alert_time = time.time()

                return should_alert, failure_type
        return False, "unknown"

    def send_weight_failure_alert(self, error_msg: str, failure_type: str, netuid: int = None):
        """Send contextual Slack alert for weight setting failure"""
        if not self.enabled or self.is_miner:
            return

        consecutive = getattr(self, '_weight_consecutive_failures', 0)
        time_since_success = time.time() - getattr(self, '_weight_last_success_time', time.time())
        hours_since_success = time_since_success / 3600

        # Build alert message based on failure type
        if "maximum recursion depth exceeded" in error_msg.lower():
            message = (f"🚨 CRITICAL: Weight setting recursion error\n"
                      f"Hotkey: ...{self.hotkey[-8:]}\n"
                      f"Netuid: {netuid}\n"
                      f"Error: {error_msg}\n"
                      f"This indicates a serious code issue that needs immediate attention.")

        elif "invalid transaction" in error_msg.lower():
            message = (f"🚨 CRITICAL: Subtensor rejected weight transaction\n"
                      f"Hotkey: ...{self.hotkey[-8:]}\n"
                      f"Netuid: {netuid}\n"
                      f"Error: {error_msg}\n"
                      f"This may indicate wallet/balance issues or network problems.")

        elif failure_type == "unknown":
            message = (f"❓ NEW PATTERN: Unknown weight setting failure\n"
                      f"Hotkey: ...{self.hotkey[-8:]}\n"
                      f"Netuid: {netuid}\n"
                      f"Consecutive failures: {consecutive}\n"
                      f"Error: {error_msg}\n"
                      f"This is a new error pattern that needs investigation.")

        else:
            # Prolonged failure alert
            if hours_since_success >= 2:
                urgency = "🚨 URGENT"
                time_msg = f"No successful weight setting in {hours_since_success:.1f} hours"
            else:
                urgency = "⚠️ WARNING"
                time_msg = f"No successful weight setting in {hours_since_success:.1f} hours"

            message = (f"{urgency}: Weight setting issues detected\n"
                      f"Hotkey: ...{self.hotkey[-8:]}\n"
                      f"Netuid: {netuid}\n"
                      f"{time_msg}\n"
                      f"Last error: {error_msg}")

        self.send_message(message, level="error")

    def send_weight_recovery_alert(self, netuid: int = None):
        """Send recovery alert after critical weight setting failures"""
        if not self.enabled or self.is_miner:
            return

        message = (f"✅ Weight setting recovered after failures\n"
                  f"Hotkey: ...{self.hotkey[-8:]}\n"
                  f"Netuid: {netuid}")

        self.send_message(message, level="info")

    def record_no_permit_event(self):
        """Record a no permit event for validators"""
        if not self.is_miner:
            with self.daily_summary_lock:
                self.daily_metrics["no_permit_events"] += 1

    def record_registration_failure(self):
        """Record a registration failure for validators"""
        if not self.is_miner:
            with self.daily_summary_lock:
                self.daily_metrics["registration_failures"] += 1

    def record_burn_uid_change(self, old_uid: int, new_uid: int):
        """Record a change in burn UID for validators"""
        if not self.is_miner:
            with self.daily_summary_lock:
                self.daily_metrics["burn_uid_changes"].append(
                    (old_uid, new_uid, datetime.now(timezone.utc))
                )

    def update_daily_metrics(self, signal_data: Dict[str, Any]):
        """Update daily metrics with signal processing data (for miners)"""
        if not self.is_miner:
            return  # This method is only for miners

        with self.daily_summary_lock:
            # Update trade pair counts
            trade_pair_id = signal_data.get("trade_pair_id", "Unknown")
            self.daily_metrics["trade_pair_counts"][trade_pair_id] += 1

            # Update validator response times (individual validator times in ms)
            if "validator_response_times" in signal_data:
                validator_times = signal_data["validator_response_times"].values()
                self.daily_metrics["validator_response_times"].extend(validator_times)

            # Update validator counts
            if "validators_attempted" in signal_data:
                self.daily_metrics["validator_counts"].append(signal_data["validators_attempted"])

            # Track successful validators
            if "validator_response_times" in signal_data:
                self.daily_metrics["successful_validators"].update(signal_data["validator_response_times"].keys())

            # Update error categories
            if signal_data.get("validator_errors"):
                for validator_hotkey, errors in signal_data["validator_errors"].items():
                    for error in errors:
                        category = self._categorize_error(error)
                        self.daily_metrics["error_categories"][category] += 1
                        self.daily_metrics["failing_validators"][validator_hotkey] += 1

            # Update signal counts
            if signal_data.get("exception"):
                self.daily_metrics["signals_failed"] += 1
            else:
                self.daily_metrics["signals_processed"] += 1
                # Update lifetime metrics
                self.lifetime_metrics["total_lifetime_signals"] += 1
                # self._save_lifetime_metrics()

    def send_signal_summary(self, summary_data: Dict[str, Any]):
        """Send a formatted signal processing summary to appropriate Slack channel"""
        if not self.enabled:
            return

        try:
            # Update daily metrics first
            self.update_daily_metrics(summary_data)

            # Determine overall status and which channel to use
            if summary_data.get("exception") or not summary_data.get('validators_succeeded'):
                status = "❌ Failed"
                color = "#ff0000"
                webhook_url = self.error_webhook_url
            elif summary_data.get("all_high_trust_succeeded", False):
                status = "✅ Success"
                color = "#00ff00"
                webhook_url = self.webhook_url
            else:
                status = "⚠️ Partial Success"
                color = "#ff9900"
                webhook_url = self.error_webhook_url

            # Build enhanced fields
            fields = [
                {
                    "title": "Status | Trade Pair",
                    "value": status + " | " + summary_data.get("trade_pair_id", "Unknown"),
                    "short": True
                },
                {
                    "title": f"{self.node_type} Hotkey | Order UUID",
                    "value": "..." + summary_data.get("miner_hotkey", "Unknown")[
                                     -8:] + f" | {summary_data.get('signal_uuid', 'Unknown')[:12]}...",
                },
                {
                    "title": "VM IP | Script Uptime",
                    "value": f"{self.vm_ip} | {self._get_uptime_str()}",
                    "short": True
                },
                {
                    "title": "Validators (succeeded/attempted)",
                    "value": f"{summary_data.get('validators_succeeded', 0)}/{summary_data.get('validators_attempted', 0)}",
                    "short": True
                }
            ]

            # Add error categorization if present
            if summary_data.get("validator_errors"):
                error_categories = defaultdict(int)
                for validator_errors in summary_data["validator_errors"].values():
                    for error in validator_errors:
                        category = self._categorize_error(error)
                        error_categories[category] += 1

                if error_categories:
                    error_summary = ", ".join([f"{cat}: {count}" for cat, count in error_categories.items()])
                    error_messages_truncated = []
                    for e in summary_data.get("validator_errors", {}).values():
                        e = str(e)
                        if len(e) > 100:
                            error_messages_truncated.append(e[100:300])
                        else:
                            error_messages_truncated.append(e)
                    fields.append({
                        "title": "🔍 Error Info",
                        "value": error_summary + "\n" + "\n".join(error_messages_truncated),
                        "short": False
                    })

            # Add validator response times if present
            if summary_data.get("validator_response_times"):
                response_times = summary_data["validator_response_times"]
                unique_times = set(response_times.values())

                if len(unique_times) > len(response_times) * 0.3:
                    # Granular per-validator times
                    sorted_times = sorted(response_times.items(), key=lambda x: x[1], reverse=True)
                    response_time_str = "Individual validator response times:\n"
                    for validator, time_taken in sorted_times[:10]:
                        response_time_str += f"• ...{validator[-8:]}: {time_taken}ms\n"
                    if len(sorted_times) > 10:
                        response_time_str += f"... and {len(sorted_times) - 10} more validators"
                else:
                    # Batch processing times
                    time_groups = defaultdict(list)
                    for validator, time_taken in response_times.items():
                        time_groups[time_taken].append(validator)

                    sorted_groups = sorted(time_groups.items(), key=lambda x: x[0], reverse=True)
                    response_time_str = "Response times by retry attempt:\n"
                    for time_taken, validators in sorted_groups:
                        validator_count = len(validators)
                        example_validators = ", ".join(["..." + v[-8:] for v in validators[:3]])
                        if validator_count > 3:
                            example_validators += f" (+{validator_count - 3} more)"
                        response_time_str += f"• {time_taken}ms: {validator_count} validators ({example_validators})\n"

                fields.append({
                    "title": "⏱️ Validator Response Times",
                    "value": response_time_str.strip(),
                    "short": False
                })

                avg_time = summary_data.get("average_response_time", 0)
                if avg_time > 0:
                    fields.append({
                        "title": "Avg Response",
                        "value": f"{avg_time}ms",
                        "short": True
                    })

            # Add error details if present
            if summary_data.get("exception"):
                fields.append({
                    "title": "💥 Error Details",
                    "value": str(summary_data["exception"])[:200],
                    "short": False
                })

            payload = {
                "attachments": [{
                    "color": color,
                    "title": f"Signal Processing Summary - {status}",
                    "fields": fields,
                    "footer": f"Taoshi {self.node_type} Monitor",
                    "ts": int(time.time())
                }]
            }

            response = requests.post(webhook_url, json=payload, timeout=10)
            response.raise_for_status()

        except Exception as e:
            bt.logging.error(f"Failed to send Slack summary: {e}")

    def shutdown(self):
        """Clean shutdown - save metrics"""
        try:
            self._save_lifetime_metrics()
        except Exception as e:
            bt.logging.error(f"Error during shutdown: {e}")