package main

import "go.mongodb.org/mongo-driver/v2/bson"

// maxDemoDays mirrors MAX_DEMO_DAYS in mielenosoitukset_fi/utils/multi_day.py:
// a demonstration's end_date is at most this many days after its start, minus one.
const maxDemoDays = 14

// overlapDates selects demonstrations that run on at least one day of
// [from, to] (either bound may be empty for open-ended). A demonstration runs
// from date to end_date (or only on date). The date bound stays an index
// range: a demo that started up to maxDemoDays-1 days before from can still
// be running, and the returned clause checks that it is.
func overlapDates(from, to string) (bson.M, bson.M) {
	dates := bson.M{}
	if to != "" {
		dates["$lte"] = to
	}
	if from == "" {
		return dates, nil
	}
	dates["$gte"] = shiftDay(from, -(maxDemoDays - 1))
	running := bson.M{"$or": bson.A{
		bson.M{"date": bson.M{"$gte": from}},
		bson.M{"end_date": bson.M{"$gte": from}},
	}}
	return dates, running
}

// demoDaysStage expands each demonstration into the days it runs, as
// "_day" strings, for per-day counts. Invalid or earlier end dates count as
// one day, and a range never exceeds maxDemoDays.
func demoDaysStages() []bson.D {
	start := bson.M{"$dateFromString": bson.M{"dateString": "$date", "format": "%Y-%m-%d", "onError": nil, "onNull": nil}}
	end := bson.M{"$dateFromString": bson.M{"dateString": "$end_date", "format": "%Y-%m-%d", "onError": nil, "onNull": nil}}
	span := bson.M{"$cond": bson.A{
		bson.M{"$and": bson.A{"$$e", bson.M{"$gt": bson.A{"$$e", "$$s"}}}},
		bson.M{"$min": bson.A{maxDemoDays - 1, bson.M{"$dateDiff": bson.M{"startDate": "$$s", "endDate": "$$e", "unit": "day"}}}},
		0,
	}}
	days := bson.M{"$let": bson.M{
		"vars": bson.M{"s": start, "e": end},
		"in": bson.M{"$cond": bson.A{
			bson.M{"$eq": bson.A{"$$s", nil}},
			bson.A{"$date"},
			bson.M{"$map": bson.M{
				"input": bson.M{"$range": bson.A{0, bson.M{"$add": bson.A{span, 1}}}},
				"as":    "i",
				"in": bson.M{"$dateToString": bson.M{"format": "%Y-%m-%d", "date": bson.M{
					"$add": bson.A{"$$s", bson.M{"$multiply": bson.A{"$$i", 86400000}}}}}},
			}},
		}},
	}}
	return []bson.D{
		{{Key: "$addFields", Value: bson.M{"_day": days}}},
		{{Key: "$unwind", Value: "$_day"}},
	}
}
