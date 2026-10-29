# Interpreting a failure prediction

## Target definition

For a machine operating at time t, failure_in_next_1h is one if a failure starts in the interval (t, t + one hour]. A machine already stopped or undergoing maintenance is outside this prediction target. Incomplete future observation is not labeled as a negative outcome.

## Score and uncertainty

The returned risk score comes from a model trained on synthetic data. It is not a calibrated probability for real industrial pumps. The assistant must identify the model version, feature timestamp and data provenance. It must not convert a high score into a claim that a physical failure is certain.

## Stopped machines

The Feature API can return monitoring information for a stopped machine. A prediction request for that machine returns HTTP 409 because the prediction target is defined only for operating machines. HTTP 409 does not mean the machine data was lost.

## Expired or missing features

The demo feature cache has a preparation lease in addition to physical Redis expiry. Once either freshness guard fails, prediction must fail rather than reuse an old vector as fresh input. Restore the demo through the documented Feast materialization procedure. Never extend a receipt by editing its timestamp.

## Candidate versus serving model

A newly registered training candidate does not automatically replace the selected serving model. The assistant must use the version actually returned by the API. It must not assume that the largest registry version number is deployed.
