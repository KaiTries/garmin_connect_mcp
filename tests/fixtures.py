"""Payloads shaped after the Personal API's OpenAPI models (field names as Garmin sends them)."""

DAILY_SUMMARIES = {
    "data": [
        {
            "calendarDate": "2026-10-26",
            "heartRate": {"minValue": 44, "maxValue": 158, "restingValue": 46},
            "stress": {"avgLevel": 28, "maxLevel": 91, "qualifier": "balanced"},
            "bodyBattery": {"minValue": 18, "maxValue": 82, "chargedValue": 60, "drainedValue": 70, "endOfDayValue": 21},
            "intensityMinutes": {"goal": 150, "moderate": 12, "vigorous": 48},
            "movement": {"steps": 11234},
            "respiration": {"avgValue": 14.2},
            "pulseOx": {"avgValue": 96},
        },
        {
            "calendarDate": "2026-10-27",
            "heartRate": {"restingValue": 49},
            "stress": {"avgLevel": -1, "maxLevel": -2},  # Garmin's "not enough data"
            "bodyBattery": {"maxValue": 64},
            "movement": {"steps": 6012},
        },
    ]
}

SLEEP_SUMMARIES = [
    {
        "calendarDate": "2026-10-27",
        "sleepTimeSeconds": 26_100,
        "deepSleepSeconds": 5_400,
        "lightSleepSeconds": 13_500,
        "remSleepSeconds": 7_200,
        "awakeSleepSeconds": 900,
        "awakeCount": 2,
        "sleepStartTimestampLocal": 1_793_053_800_000,  # 2026-10-26 22:30 local wall time
        "sleepEndTimestampLocal": 1_793_082_600_000,  # 2026-10-27 06:30
        "avgHeartRate": 47.6,
        "avgSleepStress": 14.0,
        "sleepScores": {"overall": {"value": 81, "qualifierKey": "GOOD"}, "deepPercentage": {"value": 21, "qualifierKey": "EXCELLENT"}},
        "sleepNeed": {"actual": 480},
        "sleepScoreFeedback": "POSITIVE_LONG_AND_DEEP",
    }
]

HRV_SUMMARIES = {
    "hrvSummaries": [
        {"calendarDate": "2026-10-26", "lastNightAvg": 58, "lastNight5MinHigh": 81, "weeklyAvg": 61, "feedbackPhrase": "HRV_BALANCED_6"},
        {"calendarDate": "2026-10-27", "lastNightAvg": 52, "weeklyAvg": 60},
    ]
}

RUN = {
    "activityId": 111,
    "activityName": "Zürich - Easy Run",
    "startTimeLocal": "2026-10-27 07:05:12",
    "activityType": {"typeKey": "running", "typeId": 1},
    "distance": 10_020.0,
    "duration": 3_000.0,
    "movingDuration": 2_980.0,
    "elevationGain": 54.0,
    "averageSpeed": 3.34,
    "averageHR": 142.3,
    "maxHR": 161.0,
    "activityTrainingLoad": 87.4,
    "aerobicTrainingEffect": 2.9,
    "anaerobicTrainingEffect": 0.3,
    "trainingEffectLabel": "AEROBIC_BASE",
    "hrTimeInZone_1": 300.0,
    "hrTimeInZone_2": 2_100.0,
    "hrTimeInZone_3": 600.0,
    "ownerProfileImageUrlLarge": "https://example/img.png",
}

SWIM = {
    "activityId": 112,
    "activityName": "Pool Swim",
    "startTimeLocal": "2026-10-26 18:00:00",
    "activityType": {"typeKey": "lap_swimming"},
    "distance": 2_500.0,
    "duration": 2_700.0,
    "averageSpeed": 0.9259,  # 1:48 /100m
    "poolLength": 25.0,
    "averageSwolf": 38,
}

RIDE = {
    "activityId": 113,
    "activityName": "Indoor Ride",
    "startTimeLocal": "2026-10-20 18:00:00",
    "activityType": {"typeKey": "virtual_ride"},
    "duration": 3_600.0,
    "distance": 32_000.0,
    "averageSpeed": 8.89,
    "avgPower": 201.0,
    "normPower": 214.0,
    "trainingStressScore": 63.2,
    "intensityFactor": 0.81,
    "activityTrainingLoad": 95.0,
    "hrTimeInZone_2": 2_400.0,
    "hrTimeInZone_3": 1_200.0,
}

TRI_CHILD = {**RIDE, "activityId": 114, "parentId": 999, "startTimeLocal": "2026-10-21 08:00:00"}

ACTIVITY_DETAIL = {
    "activityName": "Brick",
    "activityTypeDTO": {"typeKey": "cycling"},
    "description": "Brick: 30' bike + 10' run",
    "summaryDTO": {
        "startTimeLocal": "2026-10-27T17:00:00.0",
        "distance": 15_500.0,
        "duration": 1_800.0,
        "averagePower": 205.0,
        "normalizedPower": 214.0,
        "trainingStressScore": 38.5,
        "averageHR": 133.0,
    },
    "splitSummaries": [{"splitType": "INTERVAL_ACTIVE", "noOfSplits": 2, "duration": 1_800.0}],
}

HR_ZONES = [
    {"sport": "DEFAULT", "trainingMethod": "HR_RESERVE", "zone1Floor": 110, "zone2Floor": 130, "zone3Floor": 145, "zone4Floor": 160, "zone5Floor": 172, "maxHeartRateUsed": 185},
]

WEIGHT_RANGE = {
    "dailyWeightSummaries": [
        {"summaryDate": "2026-10-25", "latestWeight": {"weight": 72_450.0, "bodyFat": 13.1, "muscleMass": 35_200, "sourceType": "INDEX_SCALE"}},
        {"summaryDate": "2026-10-20", "latestWeight": {"weight": 72_900.0}},
    ],
    "totalAverage": {"weight": 72_675.0, "bmi": 22.4},
}
