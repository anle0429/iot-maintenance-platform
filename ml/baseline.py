"""Train Spark logistic regression with train-only preprocessing and validation-only threshold choice."""

from pathlib import Path

from pyspark.ml import Pipeline
from pyspark.ml.classification import LogisticRegression
from pyspark.ml.evaluation import BinaryClassificationEvaluator
from pyspark.ml.feature import StandardScaler, VectorAssembler
from pyspark.sql import DataFrame, functions as F

from ml.evaluation import choose_threshold, classification_metrics
from pipelines.features import FEATURE_COLUMNS
from pipelines.transforms import require


def positive_probability(vector) -> float:
    """Extract class-one probability without importing optional pandas/Arrow dependencies."""
    return float(vector[1])


def train_baseline(dataset: DataFrame, output: Path) -> tuple[dict, DataFrame]:
    """Fit on train, select one threshold on validation, then evaluate the test partition once."""
    counts = {row["split"]: {"rows": row["rows"], "positive": row["positive"]}
              for row in dataset.groupBy("split").agg(F.count("*").alias("rows"), F.sum("failure_in_next_1h").alias("positive")).collect()}
    for partition in ("train", "validation"):
        require(partition in counts and 0 < counts[partition]["positive"] < counts[partition]["rows"],
                f"{partition} must contain both classes for this baseline")
    require("test" in counts and counts["test"]["rows"] > 0, "test partition must be nonempty")
    train = dataset.filter("split='train'")
    total, positives = counts["train"]["rows"], counts["train"]["positive"]
    train = train.withColumn("class_weight", F.when(F.col("failure_in_next_1h") == 1, total / (2 * positives)).otherwise(total / (2 * (total - positives))))
    estimator = Pipeline(stages=[
        VectorAssembler(inputCols=FEATURE_COLUMNS, outputCol="raw_features", handleInvalid="error"),
        StandardScaler(inputCol="raw_features", outputCol="features", withMean=True, withStd=True),
        LogisticRegression(featuresCol="features", labelCol="failure_in_next_1h", weightCol="class_weight", maxIter=40, regParam=0.1),
    ])
    model = estimator.fit(train)
    evaluator = BinaryClassificationEvaluator(labelCol="failure_in_next_1h", metricName="areaUnderPR")
    probability_score = F.udf(positive_probability, "double")
    validation = model.transform(dataset.filter("split='validation'")).withColumn("risk_score", probability_score("probability")).cache()
    validation_rows = validation.select("failure_in_next_1h", "risk_score").collect()
    selected = choose_threshold([row[0] for row in validation_rows], [row[1] for row in validation_rows])
    model.stages[-1].setThreshold(selected["threshold"])
    model.write().save(str(output / "baseline-logistic-model"))
    validation_pr = evaluator.evaluate(validation)
    test = model.transform(dataset.filter("split='test'")).withColumn("risk_score", probability_score("probability")).cache()
    test_rows = test.select("failure_in_next_1h", "risk_score").collect()
    test_metrics = classification_metrics([row[0] for row in test_rows], [row[1] for row in test_rows], selected["threshold"])
    test_has_both_classes = 0 < counts["test"]["positive"] < counts["test"]["rows"]
    test_pr = evaluator.evaluate(test) if test_has_both_classes else None
    if counts["test"]["positive"] == 0:
        test_metrics["recall"] = test_metrics["f1"] = None
    predictions = test.select("machine_id", "event_timestamp", "failure_in_next_1h", "risk_score", *FEATURE_COLUMNS).withColumn("predicted_failure", (F.col("risk_score") >= selected["threshold"]).cast("int"))
    predictions.write.mode("errorifexists").parquet(str(output / "test_predictions"))
    result = {"algorithm": "Spark MLlib logistic regression", "feature_columns": FEATURE_COLUMNS,
              "split_class_counts": counts, "threshold_selection": "maximum validation F1; tie-break precision then threshold",
              "validation": {**selected, "area_under_pr": validation_pr},
              "test": {**test_metrics, "area_under_pr": test_pr, "ranking_evaluable": test_has_both_classes},
              "limitations": [] if test_has_both_classes else ["Test partition has one class; PR-AUC is undefined. Without positive cases recall/F1 cannot be evaluated."],
              "scope": "synthetic small workload; no real-equipment performance claim"}
    validation.unpersist()
    return result, predictions
