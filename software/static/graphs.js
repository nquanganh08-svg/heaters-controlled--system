(() => {
    const status = document.getElementById("graphs-status");
    const error = document.getElementById("graphs-error");
    const temperatureCanvas = document.getElementById("temperature-chart");
    const humidityCanvas = document.getElementById("humidity-chart");
    const refreshInterval = 15000;
    let refreshTimer;
    let refreshInProgress = false;
    let lastFeedSignature;
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
    const dateFormatter = new Intl.DateTimeFormat(undefined, {
        day: "numeric",
        month: "short",
        year: "numeric",
    });

    function createChart(canvas, label, color, unit) {
        return new Chart(canvas, {
            type: "line",
            data: {
                labels: [],
                datasets: [{
                    label,
                    data: [],
                    borderColor: color,
                    backgroundColor: `${color}22`,
                    borderWidth: 2,
                    pointRadius: 2,
                    pointHoverRadius: 5,
                    fill: true,
                    tension: 0.25,
                    spanGaps: true,
                }],
            },
            options: {
                maintainAspectRatio: false,
                animation: false,
                interaction: { mode: "index", intersect: false },
                plugins: {
                    legend: { display: false },
                    tooltip: {
                        callbacks: {
                            label: context => `${context.parsed.y} ${unit}`,
                        },
                    },
                },
                scales: {
                    x: {
                        grid: { display: false },
                        ticks: { maxTicksLimit: 5, maxRotation: 0 },
                        title: { display: true, text: "Time" },
                    },
                    y: {
                        title: { display: true, text: unit },
                    },
                },
            },
        });
    }

    const temperatureChart = createChart(
        temperatureCanvas,
        "Temperature",
        "#d5222a",
        "\u00b0C",
    );
    const humidityChart = createChart(
        humidityCanvas,
        "Humidity",
        "#347eb5",
        "%",
    );

    function updateChart(chart, readings, field) {
        chart.data.labels = readings.map(reading => {
            const date = new Date(reading.created_at);
            return Number.isNaN(date.getTime()) ? "" : timeFormatter.format(date);
        });
        chart.data.datasets[0].data = readings.map(reading => reading[field]);
        chart.update();
    }

    async function refreshCharts() {
        if (refreshInProgress || document.hidden) {
            return;
        }

        refreshInProgress = true;
        try {
            const response = await fetch("/api/feeds", { cache: "no-store" });
            const payload = await response.json();
            if (!response.ok) {
                throw new Error(payload.error || "Unable to load live sensor data.");
            }
            if (!Array.isArray(payload.feeds)) {
                throw new Error("The live sensor feed returned invalid data.");
            }

            const signature = JSON.stringify(payload.feeds);
            if (signature !== lastFeedSignature) {
                updateChart(temperatureChart, payload.feeds, "temperature");
                updateChart(humidityChart, payload.feeds, "humidity");
                lastFeedSignature = signature;
            }

            const latestReading = payload.feeds[payload.feeds.length - 1];
            const readingDate = latestReading
                ? new Date(latestReading.created_at)
                : null;
            status.textContent = latestReading && !Number.isNaN(readingDate.getTime())
                ? `Showing ${payload.feeds.length} recent readings · Latest sensor reading ${dateFormatter.format(readingDate)} ${timeFormatter.format(readingDate)}`
                : latestReading
                    ? `Showing ${payload.feeds.length} recent readings`
                    : "No sensor readings are available yet.";
            error.hidden = true;
        } catch (cause) {
            error.textContent = cause.message;
            error.hidden = false;
            status.textContent = "Live sensor data could not be refreshed.";
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
        if (!document.hidden) {
            refreshCharts();
        }
    });
})();
