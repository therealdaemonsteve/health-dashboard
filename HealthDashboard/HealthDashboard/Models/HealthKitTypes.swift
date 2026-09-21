import HealthKit

/// Determines how a HealthKit type is fetched and de-duplicated.
/// - `cumulativeSum`: Multiple sources (iPhone + Watch) contribute overlapping samples.
///   Uses `HKStatisticsCollectionQuery` with `.cumulativeSum` to get correct daily totals.
/// - `discreteAverage`: High-frequency readings (e.g. heart rate every 5s).
///   Uses `HKStatisticsCollectionQuery` with `.discreteAverage` to get daily average.
/// - `nil` (no aggregation): Individual samples that don't overlap between sources
///   (body measurements, blood pressure, sleep, workouts). Uses `HKAnchoredObjectQuery`.
enum AggregationStrategy {
    case cumulativeSum
    case discreteAverage
}

struct HealthKitTypeConfig {
    let sampleType: HKSampleType
    let metricKey: String
    let unit: HKUnit
    let unitString: String
    let enableBackgroundDelivery: Bool
    let backgroundDeliveryFrequency: HKUpdateFrequency
    let aggregation: AggregationStrategy?

    init(
        sampleType: HKSampleType,
        metricKey: String,
        unit: HKUnit,
        unitString: String,
        enableBackgroundDelivery: Bool,
        backgroundDeliveryFrequency: HKUpdateFrequency,
        aggregation: AggregationStrategy? = nil
    ) {
        self.sampleType = sampleType
        self.metricKey = metricKey
        self.unit = unit
        self.unitString = unitString
        self.enableBackgroundDelivery = enableBackgroundDelivery
        self.backgroundDeliveryFrequency = backgroundDeliveryFrequency
        self.aggregation = aggregation
    }
}

enum HealthKitTypeRegistry {

    // MARK: - Quantity Types

    static let quantityConfigs: [HealthKitTypeConfig] = [
        // Cardiovascular — heart rate aggregated daily to avoid thousands of raw samples
        .init(
            sampleType: HKQuantityType(.heartRate),
            metricKey: "heartRate",
            unit: HKUnit.count().unitDivided(by: .minute()),
            unitString: "count/min",
            enableBackgroundDelivery: false,
            backgroundDeliveryFrequency: .hourly,
            aggregation: .discreteAverage
        ),
        // These are already daily computed values from Apple — no duplication
        .init(
            sampleType: HKQuantityType(.restingHeartRate),
            metricKey: "restingHeartRate",
            unit: HKUnit.count().unitDivided(by: .minute()),
            unitString: "count/min",
            enableBackgroundDelivery: false,
            backgroundDeliveryFrequency: .hourly
        ),
        .init(
            sampleType: HKQuantityType(.heartRateVariabilitySDNN),
            metricKey: "heartRateVariabilitySDNN",
            unit: HKUnit.secondUnit(with: .milli),
            unitString: "ms",
            enableBackgroundDelivery: false,
            backgroundDeliveryFrequency: .hourly
        ),
        .init(
            sampleType: HKQuantityType(.walkingHeartRateAverage),
            metricKey: "walkingHeartRateAverage",
            unit: HKUnit.count().unitDivided(by: .minute()),
            unitString: "count/min",
            enableBackgroundDelivery: false,
            backgroundDeliveryFrequency: .hourly
        ),
        .init(
            sampleType: HKQuantityType(.vo2Max),
            metricKey: "vo2Max",
            unit: HKUnit(from: "mL/min·kg"),
            unitString: "mL/min/kg",
            enableBackgroundDelivery: false,
            backgroundDeliveryFrequency: .hourly
        ),
        .init(
            sampleType: HKQuantityType(.oxygenSaturation),
            metricKey: "oxygenSaturation",
            unit: .percent(),
            unitString: "%",
            enableBackgroundDelivery: false,
            backgroundDeliveryFrequency: .hourly
        ),
        .init(
            sampleType: HKQuantityType(.respiratoryRate),
            metricKey: "respiratoryRate",
            unit: HKUnit.count().unitDivided(by: .minute()),
            unitString: "count/min",
            enableBackgroundDelivery: false,
            backgroundDeliveryFrequency: .hourly
        ),
        .init(
            sampleType: HKQuantityType(.bodyTemperature),
            metricKey: "bodyTemperature",
            unit: .degreeCelsius(),
            unitString: "degC",
            enableBackgroundDelivery: false,
            backgroundDeliveryFrequency: .hourly
        ),
        .init(
            sampleType: HKQuantityType(.bloodPressureSystolic),
            metricKey: "bloodPressureSystolic",
            unit: .millimeterOfMercury(),
            unitString: "mmHg",
            enableBackgroundDelivery: false,
            backgroundDeliveryFrequency: .hourly
        ),
        .init(
            sampleType: HKQuantityType(.bloodPressureDiastolic),
            metricKey: "bloodPressureDiastolic",
            unit: .millimeterOfMercury(),
            unitString: "mmHg",
            enableBackgroundDelivery: false,
            backgroundDeliveryFrequency: .hourly
        ),

        // Activity — cumulative types where iPhone + Watch both contribute overlapping samples
        .init(
            sampleType: HKQuantityType(.stepCount),
            metricKey: "stepCount",
            unit: .count(),
            unitString: "count",
            enableBackgroundDelivery: false,
            backgroundDeliveryFrequency: .hourly,
            aggregation: .cumulativeSum
        ),
        .init(
            sampleType: HKQuantityType(.distanceWalkingRunning),
            metricKey: "distanceWalkingRunning",
            unit: .meterUnit(with: .kilo),
            unitString: "km",
            enableBackgroundDelivery: false,
            backgroundDeliveryFrequency: .hourly,
            aggregation: .cumulativeSum
        ),
        .init(
            sampleType: HKQuantityType(.distanceCycling),
            metricKey: "distanceCycling",
            unit: .meterUnit(with: .kilo),
            unitString: "km",
            enableBackgroundDelivery: false,
            backgroundDeliveryFrequency: .hourly,
            aggregation: .cumulativeSum
        ),
        .init(
            sampleType: HKQuantityType(.activeEnergyBurned),
            metricKey: "activeEnergyBurned",
            unit: .kilocalorie(),
            unitString: "kcal",
            enableBackgroundDelivery: false,
            backgroundDeliveryFrequency: .hourly,
            aggregation: .cumulativeSum
        ),
        .init(
            sampleType: HKQuantityType(.basalEnergyBurned),
            metricKey: "basalEnergyBurned",
            unit: .kilocalorie(),
            unitString: "kcal",
            enableBackgroundDelivery: false,
            backgroundDeliveryFrequency: .hourly,
            aggregation: .cumulativeSum
        ),
        .init(
            sampleType: HKQuantityType(.flightsClimbed),
            metricKey: "flightsClimbed",
            unit: .count(),
            unitString: "count",
            enableBackgroundDelivery: false,
            backgroundDeliveryFrequency: .hourly,
            aggregation: .cumulativeSum
        ),

        // Body Measurements — individual recorded values, no duplication
        .init(
            sampleType: HKQuantityType(.bodyMass),
            metricKey: "bodyMass",
            unit: .gramUnit(with: .kilo),
            unitString: "kg",
            enableBackgroundDelivery: false,
            backgroundDeliveryFrequency: .hourly
        ),
        .init(
            sampleType: HKQuantityType(.bodyFatPercentage),
            metricKey: "bodyFatPercentage",
            unit: .percent(),
            unitString: "%",
            enableBackgroundDelivery: false,
            backgroundDeliveryFrequency: .hourly
        ),
        .init(
            sampleType: HKQuantityType(.leanBodyMass),
            metricKey: "leanBodyMass",
            unit: .gramUnit(with: .kilo),
            unitString: "kg",
            enableBackgroundDelivery: false,
            backgroundDeliveryFrequency: .hourly
        ),
        .init(
            sampleType: HKQuantityType(.bodyMassIndex),
            metricKey: "bodyMassIndex",
            unit: .count(),
            unitString: "count",
            enableBackgroundDelivery: false,
            backgroundDeliveryFrequency: .hourly
        ),
        .init(
            sampleType: HKQuantityType(.height),
            metricKey: "height",
            unit: .meterUnit(with: .centi),
            unitString: "cm",
            enableBackgroundDelivery: false,
            backgroundDeliveryFrequency: .hourly
        ),
        .init(
            sampleType: HKQuantityType(.waistCircumference),
            metricKey: "waistCircumference",
            unit: .meterUnit(with: .centi),
            unitString: "cm",
            enableBackgroundDelivery: false,
            backgroundDeliveryFrequency: .hourly
        ),

        // Nutrition — cumulative, de-duplicate across sources
        .init(
            sampleType: HKQuantityType(.dietaryEnergyConsumed),
            metricKey: "dietaryEnergyConsumed",
            unit: .kilocalorie(),
            unitString: "kcal",
            enableBackgroundDelivery: true,
            backgroundDeliveryFrequency: .immediate,
            aggregation: .cumulativeSum
        ),
        .init(
            sampleType: HKQuantityType(.dietaryProtein),
            metricKey: "dietaryProtein",
            unit: .gram(),
            unitString: "g",
            enableBackgroundDelivery: false,
            backgroundDeliveryFrequency: .hourly,
            aggregation: .cumulativeSum
        ),
        .init(
            sampleType: HKQuantityType(.dietaryCarbohydrates),
            metricKey: "dietaryCarbohydrates",
            unit: .gram(),
            unitString: "g",
            enableBackgroundDelivery: false,
            backgroundDeliveryFrequency: .hourly,
            aggregation: .cumulativeSum
        ),
        .init(
            sampleType: HKQuantityType(.dietaryFatTotal),
            metricKey: "dietaryFatTotal",
            unit: .gram(),
            unitString: "g",
            enableBackgroundDelivery: false,
            backgroundDeliveryFrequency: .hourly,
            aggregation: .cumulativeSum
        ),
        .init(
            sampleType: HKQuantityType(.dietaryFatSaturated),
            metricKey: "dietaryFatSaturated",
            unit: .gram(),
            unitString: "g",
            enableBackgroundDelivery: false,
            backgroundDeliveryFrequency: .hourly,
            aggregation: .cumulativeSum
        ),
        .init(
            sampleType: HKQuantityType(.dietaryFatMonounsaturated),
            metricKey: "dietaryFatMonounsaturated",
            unit: .gram(),
            unitString: "g",
            enableBackgroundDelivery: false,
            backgroundDeliveryFrequency: .hourly,
            aggregation: .cumulativeSum
        ),
        .init(
            sampleType: HKQuantityType(.dietaryFatPolyunsaturated),
            metricKey: "dietaryFatPolyunsaturated",
            unit: .gram(),
            unitString: "g",
            enableBackgroundDelivery: false,
            backgroundDeliveryFrequency: .hourly,
            aggregation: .cumulativeSum
        ),
        .init(
            sampleType: HKQuantityType(.dietaryFiber),
            metricKey: "dietaryFiber",
            unit: .gram(),
            unitString: "g",
            enableBackgroundDelivery: false,
            backgroundDeliveryFrequency: .hourly,
            aggregation: .cumulativeSum
        ),
        .init(
            sampleType: HKQuantityType(.dietarySugar),
            metricKey: "dietarySugar",
            unit: .gram(),
            unitString: "g",
            enableBackgroundDelivery: false,
            backgroundDeliveryFrequency: .hourly,
            aggregation: .cumulativeSum
        ),
        .init(
            sampleType: HKQuantityType(.dietarySodium),
            metricKey: "dietarySodium",
            unit: .gramUnit(with: .milli),
            unitString: "mg",
            enableBackgroundDelivery: false,
            backgroundDeliveryFrequency: .hourly,
            aggregation: .cumulativeSum
        ),
        .init(
            sampleType: HKQuantityType(.dietaryWater),
            metricKey: "dietaryWater",
            unit: .liter(),
            unitString: "L",
            enableBackgroundDelivery: false,
            backgroundDeliveryFrequency: .hourly,
            aggregation: .cumulativeSum
        ),
        .init(
            sampleType: HKQuantityType(.dietaryCaffeine),
            metricKey: "dietaryCaffeine",
            unit: .gramUnit(with: .milli),
            unitString: "mg",
            enableBackgroundDelivery: false,
            backgroundDeliveryFrequency: .hourly,
            aggregation: .cumulativeSum
        ),

        // Dietary Micronutrients — cumulative
        .init(
            sampleType: HKQuantityType(.dietaryCholesterol),
            metricKey: "dietaryCholesterol",
            unit: .gramUnit(with: .milli),
            unitString: "mg",
            enableBackgroundDelivery: false,
            backgroundDeliveryFrequency: .hourly,
            aggregation: .cumulativeSum
        ),
        .init(
            sampleType: HKQuantityType(.dietaryCalcium),
            metricKey: "dietaryCalcium",
            unit: .gramUnit(with: .milli),
            unitString: "mg",
            enableBackgroundDelivery: false,
            backgroundDeliveryFrequency: .hourly,
            aggregation: .cumulativeSum
        ),
        .init(
            sampleType: HKQuantityType(.dietaryIron),
            metricKey: "dietaryIron",
            unit: .gramUnit(with: .milli),
            unitString: "mg",
            enableBackgroundDelivery: false,
            backgroundDeliveryFrequency: .hourly,
            aggregation: .cumulativeSum
        ),
        .init(
            sampleType: HKQuantityType(.dietaryPotassium),
            metricKey: "dietaryPotassium",
            unit: .gramUnit(with: .milli),
            unitString: "mg",
            enableBackgroundDelivery: false,
            backgroundDeliveryFrequency: .hourly,
            aggregation: .cumulativeSum
        ),
        .init(
            sampleType: HKQuantityType(.dietaryVitaminC),
            metricKey: "dietaryVitaminC",
            unit: .gramUnit(with: .milli),
            unitString: "mg",
            enableBackgroundDelivery: false,
            backgroundDeliveryFrequency: .hourly,
            aggregation: .cumulativeSum
        ),
        .init(
            sampleType: HKQuantityType(.dietaryVitaminD),
            metricKey: "dietaryVitaminD",
            unit: .gramUnit(with: .micro),
            unitString: "mcg",
            enableBackgroundDelivery: false,
            backgroundDeliveryFrequency: .hourly,
            aggregation: .cumulativeSum
        ),
        .init(
            sampleType: HKQuantityType(.dietaryMagnesium),
            metricKey: "dietaryMagnesium",
            unit: .gramUnit(with: .milli),
            unitString: "mg",
            enableBackgroundDelivery: false,
            backgroundDeliveryFrequency: .hourly,
            aggregation: .cumulativeSum
        ),
        .init(
            sampleType: HKQuantityType(.dietaryZinc),
            metricKey: "dietaryZinc",
            unit: .gramUnit(with: .milli),
            unitString: "mg",
            enableBackgroundDelivery: false,
            backgroundDeliveryFrequency: .hourly,
            aggregation: .cumulativeSum
        ),
        .init(
            sampleType: HKQuantityType(.dietaryFolate),
            metricKey: "dietaryFolate",
            unit: .gramUnit(with: .micro),
            unitString: "mcg",
            enableBackgroundDelivery: false,
            backgroundDeliveryFrequency: .hourly,
            aggregation: .cumulativeSum
        ),
        .init(
            sampleType: HKQuantityType(.dietaryVitaminA),
            metricKey: "dietaryVitaminA",
            unit: .gramUnit(with: .micro),
            unitString: "mcg",
            enableBackgroundDelivery: false,
            backgroundDeliveryFrequency: .hourly,
            aggregation: .cumulativeSum
        ),
        .init(
            sampleType: HKQuantityType(.dietaryVitaminB12),
            metricKey: "dietaryVitaminB12",
            unit: .gramUnit(with: .micro),
            unitString: "mcg",
            enableBackgroundDelivery: false,
            backgroundDeliveryFrequency: .hourly,
            aggregation: .cumulativeSum
        ),
        .init(
            sampleType: HKQuantityType(.dietaryVitaminB6),
            metricKey: "dietaryVitaminB6",
            unit: .gramUnit(with: .milli),
            unitString: "mg",
            enableBackgroundDelivery: false,
            backgroundDeliveryFrequency: .hourly,
            aggregation: .cumulativeSum
        ),
        .init(
            sampleType: HKQuantityType(.dietaryVitaminE),
            metricKey: "dietaryVitaminE",
            unit: .gramUnit(with: .milli),
            unitString: "mg",
            enableBackgroundDelivery: false,
            backgroundDeliveryFrequency: .hourly,
            aggregation: .cumulativeSum
        ),
        .init(
            sampleType: HKQuantityType(.dietaryVitaminK),
            metricKey: "dietaryVitaminK",
            unit: .gramUnit(with: .micro),
            unitString: "mcg",
            enableBackgroundDelivery: false,
            backgroundDeliveryFrequency: .hourly,
            aggregation: .cumulativeSum
        ),
        .init(
            sampleType: HKQuantityType(.dietaryNiacin),
            metricKey: "dietaryNiacin",
            unit: .gramUnit(with: .milli),
            unitString: "mg",
            enableBackgroundDelivery: false,
            backgroundDeliveryFrequency: .hourly,
            aggregation: .cumulativeSum
        ),
        .init(
            sampleType: HKQuantityType(.dietaryThiamin),
            metricKey: "dietaryThiamin",
            unit: .gramUnit(with: .milli),
            unitString: "mg",
            enableBackgroundDelivery: false,
            backgroundDeliveryFrequency: .hourly,
            aggregation: .cumulativeSum
        ),
        .init(
            sampleType: HKQuantityType(.dietaryRiboflavin),
            metricKey: "dietaryRiboflavin",
            unit: .gramUnit(with: .milli),
            unitString: "mg",
            enableBackgroundDelivery: false,
            backgroundDeliveryFrequency: .hourly,
            aggregation: .cumulativeSum
        ),
        .init(
            sampleType: HKQuantityType(.dietaryBiotin),
            metricKey: "dietaryBiotin",
            unit: .gramUnit(with: .micro),
            unitString: "mcg",
            enableBackgroundDelivery: false,
            backgroundDeliveryFrequency: .hourly,
            aggregation: .cumulativeSum
        ),
        .init(
            sampleType: HKQuantityType(.dietaryPantothenicAcid),
            metricKey: "dietaryPantothenicAcid",
            unit: .gramUnit(with: .milli),
            unitString: "mg",
            enableBackgroundDelivery: false,
            backgroundDeliveryFrequency: .hourly,
            aggregation: .cumulativeSum
        ),
        .init(
            sampleType: HKQuantityType(.dietaryPhosphorus),
            metricKey: "dietaryPhosphorus",
            unit: .gramUnit(with: .milli),
            unitString: "mg",
            enableBackgroundDelivery: false,
            backgroundDeliveryFrequency: .hourly,
            aggregation: .cumulativeSum
        ),
        .init(
            sampleType: HKQuantityType(.dietarySelenium),
            metricKey: "dietarySelenium",
            unit: .gramUnit(with: .micro),
            unitString: "mcg",
            enableBackgroundDelivery: false,
            backgroundDeliveryFrequency: .hourly,
            aggregation: .cumulativeSum
        ),
        .init(
            sampleType: HKQuantityType(.dietaryCopper),
            metricKey: "dietaryCopper",
            unit: .gramUnit(with: .milli),
            unitString: "mg",
            enableBackgroundDelivery: false,
            backgroundDeliveryFrequency: .hourly,
            aggregation: .cumulativeSum
        ),
        .init(
            sampleType: HKQuantityType(.dietaryManganese),
            metricKey: "dietaryManganese",
            unit: .gramUnit(with: .milli),
            unitString: "mg",
            enableBackgroundDelivery: false,
            backgroundDeliveryFrequency: .hourly,
            aggregation: .cumulativeSum
        ),
        .init(
            sampleType: HKQuantityType(.dietaryIodine),
            metricKey: "dietaryIodine",
            unit: .gramUnit(with: .micro),
            unitString: "mcg",
            enableBackgroundDelivery: false,
            backgroundDeliveryFrequency: .hourly,
            aggregation: .cumulativeSum
        ),
        .init(
            sampleType: HKQuantityType(.dietaryChromium),
            metricKey: "dietaryChromium",
            unit: .gramUnit(with: .micro),
            unitString: "mcg",
            enableBackgroundDelivery: false,
            backgroundDeliveryFrequency: .hourly,
            aggregation: .cumulativeSum
        ),

        // Mobility & Gait — auto-collected by iPhone, individual samples
        .init(
            sampleType: HKQuantityType(.walkingSpeed),
            metricKey: "walkingSpeed",
            unit: HKUnit.meter().unitDivided(by: .second()),
            unitString: "m/s",
            enableBackgroundDelivery: false,
            backgroundDeliveryFrequency: .hourly
        ),
        .init(
            sampleType: HKQuantityType(.walkingStepLength),
            metricKey: "walkingStepLength",
            unit: .meterUnit(with: .centi),
            unitString: "cm",
            enableBackgroundDelivery: false,
            backgroundDeliveryFrequency: .hourly
        ),
        .init(
            sampleType: HKQuantityType(.walkingDoubleSupportPercentage),
            metricKey: "walkingDoubleSupportPercentage",
            unit: .percent(),
            unitString: "%",
            enableBackgroundDelivery: false,
            backgroundDeliveryFrequency: .hourly
        ),
        .init(
            sampleType: HKQuantityType(.walkingAsymmetryPercentage),
            metricKey: "walkingAsymmetryPercentage",
            unit: .percent(),
            unitString: "%",
            enableBackgroundDelivery: false,
            backgroundDeliveryFrequency: .hourly
        ),
        .init(
            sampleType: HKQuantityType(.appleWalkingSteadiness),
            metricKey: "appleWalkingSteadiness",
            unit: .percent(),
            unitString: "%",
            enableBackgroundDelivery: false,
            backgroundDeliveryFrequency: .hourly
        ),
        .init(
            sampleType: HKQuantityType(.stairAscentSpeed),
            metricKey: "stairAscentSpeed",
            unit: HKUnit.meter().unitDivided(by: .second()),
            unitString: "m/s",
            enableBackgroundDelivery: false,
            backgroundDeliveryFrequency: .hourly
        ),
        .init(
            sampleType: HKQuantityType(.stairDescentSpeed),
            metricKey: "stairDescentSpeed",
            unit: HKUnit.meter().unitDivided(by: .second()),
            unitString: "m/s",
            enableBackgroundDelivery: false,
            backgroundDeliveryFrequency: .hourly
        ),
        .init(
            sampleType: HKQuantityType(.sixMinuteWalkTestDistance),
            metricKey: "sixMinuteWalkTestDistance",
            unit: .meter(),
            unitString: "m",
            enableBackgroundDelivery: false,
            backgroundDeliveryFrequency: .hourly
        ),

        // Additional Activity — cumulative types
        .init(
            sampleType: HKQuantityType(.appleExerciseTime),
            metricKey: "appleExerciseTime",
            unit: .minute(),
            unitString: "min",
            enableBackgroundDelivery: false,
            backgroundDeliveryFrequency: .hourly,
            aggregation: .cumulativeSum
        ),
        .init(
            sampleType: HKQuantityType(.appleStandTime),
            metricKey: "appleStandTime",
            unit: .minute(),
            unitString: "min",
            enableBackgroundDelivery: false,
            backgroundDeliveryFrequency: .hourly,
            aggregation: .cumulativeSum
        ),
        .init(
            sampleType: HKQuantityType(.distanceSwimming),
            metricKey: "distanceSwimming",
            unit: .meter(),
            unitString: "m",
            enableBackgroundDelivery: false,
            backgroundDeliveryFrequency: .hourly,
            aggregation: .cumulativeSum
        ),
        .init(
            sampleType: HKQuantityType(.swimmingStrokeCount),
            metricKey: "swimmingStrokeCount",
            unit: .count(),
            unitString: "count",
            enableBackgroundDelivery: false,
            backgroundDeliveryFrequency: .hourly,
            aggregation: .cumulativeSum
        ),
        .init(
            sampleType: HKQuantityType(.numberOfTimesFallen),
            metricKey: "numberOfTimesFallen",
            unit: .count(),
            unitString: "count",
            enableBackgroundDelivery: false,
            backgroundDeliveryFrequency: .hourly,
            aggregation: .cumulativeSum
        ),

        // Running Dynamics (iOS 16+) — individual samples per run
        .init(
            sampleType: HKQuantityType(.runningSpeed),
            metricKey: "runningSpeed",
            unit: HKUnit.meter().unitDivided(by: .second()),
            unitString: "m/s",
            enableBackgroundDelivery: false,
            backgroundDeliveryFrequency: .hourly
        ),
        .init(
            sampleType: HKQuantityType(.runningStrideLength),
            metricKey: "runningStrideLength",
            unit: .meter(),
            unitString: "m",
            enableBackgroundDelivery: false,
            backgroundDeliveryFrequency: .hourly
        ),
        .init(
            sampleType: HKQuantityType(.runningVerticalOscillation),
            metricKey: "runningVerticalOscillation",
            unit: .meterUnit(with: .centi),
            unitString: "cm",
            enableBackgroundDelivery: false,
            backgroundDeliveryFrequency: .hourly
        ),
        .init(
            sampleType: HKQuantityType(.runningGroundContactTime),
            metricKey: "runningGroundContactTime",
            unit: HKUnit.secondUnit(with: .milli),
            unitString: "ms",
            enableBackgroundDelivery: false,
            backgroundDeliveryFrequency: .hourly
        ),
        .init(
            sampleType: HKQuantityType(.runningPower),
            metricKey: "runningPower",
            unit: .watt(),
            unitString: "W",
            enableBackgroundDelivery: false,
            backgroundDeliveryFrequency: .hourly
        ),

        // Cycling (iOS 17+) — individual samples per ride
        .init(
            sampleType: HKQuantityType(.cyclingSpeed),
            metricKey: "cyclingSpeed",
            unit: HKUnit.meter().unitDivided(by: .second()),
            unitString: "m/s",
            enableBackgroundDelivery: false,
            backgroundDeliveryFrequency: .hourly
        ),
        .init(
            sampleType: HKQuantityType(.cyclingPower),
            metricKey: "cyclingPower",
            unit: .watt(),
            unitString: "W",
            enableBackgroundDelivery: false,
            backgroundDeliveryFrequency: .hourly
        ),
        .init(
            sampleType: HKQuantityType(.cyclingCadence),
            metricKey: "cyclingCadence",
            unit: HKUnit.count().unitDivided(by: .minute()),
            unitString: "count/min",
            enableBackgroundDelivery: false,
            backgroundDeliveryFrequency: .hourly
        ),
        .init(
            sampleType: HKQuantityType(.cyclingFunctionalThresholdPower),
            metricKey: "cyclingFunctionalThresholdPower",
            unit: .watt(),
            unitString: "W",
            enableBackgroundDelivery: false,
            backgroundDeliveryFrequency: .hourly
        ),

        // Heart Recovery
        .init(
            sampleType: HKQuantityType(.heartRateRecoveryOneMinute),
            metricKey: "heartRateRecoveryOneMinute",
            unit: HKUnit.count().unitDivided(by: .minute()),
            unitString: "count/min",
            enableBackgroundDelivery: false,
            backgroundDeliveryFrequency: .hourly
        ),

        // Vitals
        .init(
            sampleType: HKQuantityType(.appleSleepingWristTemperature),
            metricKey: "appleSleepingWristTemperature",
            unit: .degreeCelsius(),
            unitString: "degC",
            enableBackgroundDelivery: false,
            backgroundDeliveryFrequency: .hourly
        ),
        .init(
            sampleType: HKQuantityType(.bloodGlucose),
            metricKey: "bloodGlucose",
            unit: HKUnit.gramUnit(with: .milli).unitDivided(by: .literUnit(with: .deci)),
            unitString: "mg/dL",
            enableBackgroundDelivery: false,
            backgroundDeliveryFrequency: .hourly
        ),
        .init(
            sampleType: HKQuantityType(.peripheralPerfusionIndex),
            metricKey: "peripheralPerfusionIndex",
            unit: .percent(),
            unitString: "%",
            enableBackgroundDelivery: false,
            backgroundDeliveryFrequency: .hourly
        ),

        // Audio Exposure — aggregated daily average
        .init(
            sampleType: HKQuantityType(.environmentalAudioExposure),
            metricKey: "environmentalAudioExposure",
            unit: .decibelAWeightedSoundPressureLevel(),
            unitString: "dBASPL",
            enableBackgroundDelivery: false,
            backgroundDeliveryFrequency: .hourly,
            aggregation: .discreteAverage
        ),
        .init(
            sampleType: HKQuantityType(.headphoneAudioExposure),
            metricKey: "headphoneAudioExposure",
            unit: .decibelAWeightedSoundPressureLevel(),
            unitString: "dBASPL",
            enableBackgroundDelivery: false,
            backgroundDeliveryFrequency: .hourly,
            aggregation: .discreteAverage
        ),

        // Wellness
        .init(
            sampleType: HKQuantityType(.timeInDaylight),
            metricKey: "timeInDaylight",
            unit: .minute(),
            unitString: "min",
            enableBackgroundDelivery: false,
            backgroundDeliveryFrequency: .hourly,
            aggregation: .cumulativeSum
        ),
    ]

    // MARK: - Category Types

    static let sleepConfig = HealthKitTypeConfig(
        sampleType: HKCategoryType(.sleepAnalysis),
        metricKey: "sleepAnalysis",
        unit: .minute(),
        unitString: "min",
        enableBackgroundDelivery: false,
        backgroundDeliveryFrequency: .hourly
    )

    static let mindfulConfig = HealthKitTypeConfig(
        sampleType: HKCategoryType(.mindfulSession),
        metricKey: "mindfulSession",
        unit: .minute(),
        unitString: "min",
        enableBackgroundDelivery: false,
        backgroundDeliveryFrequency: .hourly
    )

    static let appleStandHourConfig = HealthKitTypeConfig(
        sampleType: HKCategoryType(.appleStandHour),
        metricKey: "appleStandHour",
        unit: .count(),
        unitString: "count",
        enableBackgroundDelivery: false,
        backgroundDeliveryFrequency: .hourly
    )

    static let highHeartRateEventConfig = HealthKitTypeConfig(
        sampleType: HKCategoryType(.highHeartRateEvent),
        metricKey: "highHeartRateEvent",
        unit: .count(),
        unitString: "count",
        enableBackgroundDelivery: false,
        backgroundDeliveryFrequency: .hourly
    )

    static let lowHeartRateEventConfig = HealthKitTypeConfig(
        sampleType: HKCategoryType(.lowHeartRateEvent),
        metricKey: "lowHeartRateEvent",
        unit: .count(),
        unitString: "count",
        enableBackgroundDelivery: false,
        backgroundDeliveryFrequency: .hourly
    )

    static let irregularHeartRhythmEventConfig = HealthKitTypeConfig(
        sampleType: HKCategoryType(.irregularHeartRhythmEvent),
        metricKey: "irregularHeartRhythmEvent",
        unit: .count(),
        unitString: "count",
        enableBackgroundDelivery: false,
        backgroundDeliveryFrequency: .hourly
    )

    // MARK: - Workout Type

    static let workoutConfig = HealthKitTypeConfig(
        sampleType: HKWorkoutType.workoutType(),
        metricKey: "workout",
        unit: .minute(),
        unitString: "min",
        enableBackgroundDelivery: true,
        backgroundDeliveryFrequency: .immediate
    )

    // MARK: - Aggregates

    static var allConfigs: [HealthKitTypeConfig] {
        quantityConfigs + [
            sleepConfig, mindfulConfig,
            appleStandHourConfig,
            highHeartRateEventConfig, lowHeartRateEventConfig, irregularHeartRhythmEventConfig,
            workoutConfig,
        ]
    }

    static var backgroundDeliveryConfigs: [HealthKitTypeConfig] {
        allConfigs.filter { $0.enableBackgroundDelivery }
    }

    static var allReadTypes: Set<HKObjectType> {
        var types = Set<HKObjectType>()
        for config in allConfigs {
            types.insert(config.sampleType)
        }
        types.insert(HKSeriesType.activitySummaryType())
        return types
    }
}
