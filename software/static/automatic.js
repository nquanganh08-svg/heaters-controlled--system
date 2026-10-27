(() => {
    const status = document.getElementById("automatic-status");
    const error = document.getElementById("automatic-error");
    const temperatureCanvas = document.getElementById("automatic-temperature-chart");
    const heaterCanvas = document.getElementById("automatic-heater-chart");
    const refreshInterval = 15000;
    let refreshTimer;
    let refreshInProgress = false;
    let lastSignature;

    if (!window.Chart) {
        status.textContent = "Charts are unavailable.";
        error.textContent = "The chart library could not be loaded.";
        error.hidden = false;
        return;
    }

    const timeFormatter = new Intl.DateTimeFormat(undefined, {
        hour: "2-digit",
        minute: "2-digit",
        hourCycle: "h23",
    });

    const temperatureChart = new Chart(temperatureCanvas, {
        type: "line",
        data: {
            labels: [],
            datasets: [
                {
                    label: "Temperature",
                    data: [],
                    borderColor: "#d5222a",
                    backgroundColor: "#d5222a22",
                    borderWidth: 2,
                    pointRadius: 2,
                    fill: true,
                    tension: 0.25,
                    spanGaps: true,
                },
                {
                    label: "Turn on below",
                    data: [],
                    borderColor: "#347eb5",
                    borderDash: [6, 4],
                    pointRadius: 0,
                    borderWidth: 1,
                },
                {
                    label: "Turn off above",
                    data: [],
                    borderColor: "#e5a323",
                    borderDash: [6, 4],
                    pointRadius: 0,
                    borderWidth: 1,
                },
            ],
        },
        options: {
            maintainAspectRatio: false,
            animation: false,
            interaction: { mode: "index", intersect: false },
            scales: {
                x: { grid: { display: false }, ticks: { maxTicksLimit: 5 } },
                y: { title: { display: true, text: "°C" } },
            },
        },
    });

    const heaterChart = new Chart(heaterCanvas, {
        type: "line",
        data: {
            labels: [],
            datasets: [{
                label: "Heater",
                data: [],
                borderColor: "#347eb5",
                backgroundColor: "#347eb522",
                borderWidth: 2,
                pointRadius: 3,
                stepped: "after",
                fill: true,
            }],
        },
        options: {
            maintainAspectRatio: false,
            animation: false,
            interaction: { mode: "index", intersect: false },
            plugins: {
                tooltip: {
                    callbacks: {
                        label: context => context.parsed.y === 1 ? "Heater on" : "Heater off",
                    },
                },
            },
            scales: {
                x: { grid: { display: false }, ticks: { maxTicksLimit: 5 } },
                y: {
                    min: 0,
                    max: 1,
                    ticks: {
                        stepSize: 1,
                        callback: value => value === 1 ? "On" : "Off",
                    },
                },
            },
        },
    });

    function formatTime(value) {
        const timestamp = value.includes("T") ? value : `${value.replace(" ", "T")}Z`;
        const date = new Date(timestamp);
        return Number.isNaN(date.getTime()) ? "" : timeFormatter.format(date);
    }

    async function refreshCharts() {
        if (refreshInProgress || document.hidden) return;
        refreshInProgress = true;
        try {
            const [feedResponse, historyResponse] = await Promise.all([
                fetch("/api/feeds", { cache: "no-store" }),
                fetch("/api/heater-history", { cache: "no-store" }),
            ]);
            const [feedPayload, historyPayload] = await Promise.all([
                feedResponse.json(),
                historyResponse.json(),
            ]);
            if (!feedResponse.ok) {
                throw new Error(feedPayload.error || "Unable to load temperature data.");
            }
            if (!historyResponse.ok) {
                throw new Error(historyPayload.error || "Unable to load heater history.");
            }
            if (!Array.isArray(feedPayload.feeds) || !Array.isArray(historyPayload.commands)) {
                throw new Error("The sensor or heater history returned invalid data.");
            }

            const signature = JSON.stringify([feedPayload.feeds, historyPayload.commands]);
            if (signature !== lastSignature) {
                const readings = feedPayload.feeds.filter(reading =>
                    reading.created_at && reading.temperature !== null
                );
                const labels = readings.map(reading => formatTime(reading.created_at));
                temperatureChart.data.labels = labels;
                temperatureChart.data.datasets[0].data =
                    readings.map(reading => reading.temperature);
                temperatureChart.data.datasets[1].data =
                    labels.map(() => window.automaticTurnOnBelow);
                temperatureChart.data.datasets[2].data =
                    labels.map(() => window.automaticTurnOffAbove);
                temperatureChart.update();

                heaterChart.data.labels = historyPayload.commands.map(command =>
                    formatTime(command.created_at)
                );
                heaterChart.data.datasets[0].data = historyPayload.commands.map(command =>
                    command.state === "on" ? 1 : 0
                );
                heaterChart.update();
                lastSignature = signature;
            }

            const latest = feedPayload.feeds[feedPayload.feeds.length - 1];
            status.textContent = latest && latest.temperature !== null
                ? `Latest temperature: ${latest.temperature} °C · Refreshes every 15 seconds`
                : "No temperature readings are available yet.";
            error.hidden = true;
        } catch (cause) {
            error.textContent = cause.message;
            error.hidden = false;
            status.textContent = "Automatic mode charts could not be refreshed.";
        } finally {
            refreshInProgress = false;
            if (!document.hidden) {
                refreshTimer = window.setTimeout(refreshCharts, refreshInterval);
            }
        }
    }

    refreshCharts();
    document.addEventListener("visibilitychange", () => {
        window.clearTimeout(refreshTimer);
        if (!document.hidden) refreshCharts();
    });
})();
