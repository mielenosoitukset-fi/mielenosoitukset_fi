package main

import (
	"context"
	"crypto/hmac"
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"io"
	"log"
	"net"
	"net/http"
	"net/url"
	"os"
	"sort"
	"strings"
	"time"

	"github.com/redis/go-redis/v9"
)

// statusCheck is one optional row of GET /api/status besides the database.
// The probe gets a context with a short deadline and its error is never shown.
type statusCheck struct {
	name, okMessage string
	probe           func(context.Context) error
}

const (
	statusCacheName   = "Välimuisti"
	statusStorageName = "Tiedostovarasto"
	statusCacheKey    = "moso:status-probe:v1"
)

// statusChecksFromEnv restores Flask's cache and file storage rows of /status,
// with Go owning its own clients. Both stay off unless configured:
//
//   - STATUS_CACHE_REDIS_URL: the Redis that Flask's cache uses (REDIS_HOST,
//     REDIS_PORT, REDIS_DB with CACHE_TYPE RedisCache). Not PUBLIC_REDIS_URL:
//     the Go response cache is a different dependency.
//   - STATUS_S3_ENDPOINT, STATUS_S3_ACCESS_KEY, STATUS_S3_SECRET_KEY and
//     optionally STATUS_S3_REGION (default us-east-1): the S3 endpoint and keys
//     from Flask's S3 settings.
//
// A bad setting disables only that check and is logged; it never stops the API.
// The returned function closes the clients.
func statusChecksFromEnv() ([]statusCheck, func()) {
	var checks []statusCheck
	closers := []func(){}
	if rawURL := os.Getenv("STATUS_CACHE_REDIS_URL"); rawURL != "" {
		check, closeClient, err := cacheStatusCheck(rawURL)
		if err != nil {
			log.Print("Status cache check disabled: ", err)
		} else {
			checks = append(checks, check)
			closers = append(closers, closeClient)
		}
	}
	config := s3StatusConfig{
		Endpoint:  os.Getenv("STATUS_S3_ENDPOINT"),
		AccessKey: os.Getenv("STATUS_S3_ACCESS_KEY"),
		SecretKey: os.Getenv("STATUS_S3_SECRET_KEY"),
		Region:    env("STATUS_S3_REGION", "us-east-1"),
	}
	if config.Endpoint != "" || config.AccessKey != "" || config.SecretKey != "" {
		check, err := storageStatusCheck(config, nil)
		if err != nil {
			log.Print("Status storage check disabled: ", err)
		} else {
			checks = append(checks, check)
		}
	}
	return checks, func() {
		for _, closeClient := range closers {
			closeClient()
		}
	}
}

// cacheStatusCheck writes a short-lived key, as Flask's cache.set("_status_ping",
// True, timeout=5) does, under its own key so no Flask cache entry is touched.
func cacheStatusCheck(rawURL string) (statusCheck, func(), error) {
	options, err := redis.ParseURL(rawURL)
	if err != nil {
		return statusCheck{}, nil, errors.New("invalid STATUS_CACHE_REDIS_URL")
	}
	options.MaxRetries = -1
	options.DialTimeout = 500 * time.Millisecond
	options.ReadTimeout = 500 * time.Millisecond
	options.WriteTimeout = 500 * time.Millisecond
	options.PoolTimeout = 500 * time.Millisecond
	options.ContextTimeoutEnabled = true
	options.PoolSize = 2
	options.Protocol = 2
	client := redis.NewClient(options)
	return statusCheck{statusCacheName, "Yhteys kunnossa", func(ctx context.Context) error {
		return client.Set(ctx, statusCacheKey, "1", 5*time.Second).Err()
	}}, func() { _ = client.Close() }, nil
}

type s3StatusConfig struct {
	Endpoint, AccessKey, SecretKey, Region string
}

// storageStatusCheck lists buckets, as Flask's _s3_client.list_buckets() does:
// a SigV4-signed GET of the endpoint root. Any 2xx answer is reachable.
func storageStatusCheck(config s3StatusConfig, client *http.Client) (statusCheck, error) {
	if config.Endpoint == "" || config.AccessKey == "" || config.SecretKey == "" || config.Region == "" {
		return statusCheck{}, errors.New("STATUS_S3_ENDPOINT, STATUS_S3_ACCESS_KEY and STATUS_S3_SECRET_KEY are all required")
	}
	endpoint, err := url.Parse(config.Endpoint)
	if err != nil || endpoint.Host == "" || endpoint.User != nil || endpoint.RawQuery != "" || endpoint.Fragment != "" {
		return statusCheck{}, errors.New("invalid STATUS_S3_ENDPOINT")
	}
	if endpoint.Scheme != "https" && !(endpoint.Scheme == "http" && isLoopbackHost(endpoint.Hostname())) {
		return statusCheck{}, errors.New("STATUS_S3_ENDPOINT must use https")
	}
	endpoint.Path = strings.TrimRight(endpoint.Path, "/") + "/"
	if client == nil {
		client = &http.Client{}
	}
	noRedirects := *client
	noRedirects.CheckRedirect = func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse }
	target := endpoint.String()
	return statusCheck{statusStorageName, "Saavutettavissa", func(ctx context.Context) error {
		request, err := http.NewRequestWithContext(ctx, http.MethodGet, target, nil)
		if err != nil {
			return err
		}
		signS3Request(request, config, clock().UTC())
		response, err := noRedirects.Do(request)
		if err != nil {
			return err
		}
		defer response.Body.Close()
		_, _ = io.Copy(io.Discard, io.LimitReader(response.Body, 64<<10))
		if response.StatusCode < 200 || response.StatusCode > 299 {
			return errors.New("storage answered " + response.Status)
		}
		return nil
	}}, nil
}

func isLoopbackHost(host string) bool {
	if host == "localhost" {
		return true
	}
	ip := net.ParseIP(host)
	return ip != nil && ip.IsLoopback()
}

const emptyPayloadSHA256 = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"

// signS3Request adds AWS Signature Version 4 headers for a request without a
// body. Every header already on the request is signed, plus host.
func signS3Request(request *http.Request, config s3StatusConfig, now time.Time) {
	amzDate := now.Format("20060102T150405Z")
	day := now.Format("20060102")
	request.Header.Set("X-Amz-Date", amzDate)
	request.Header.Set("X-Amz-Content-Sha256", emptyPayloadSHA256)

	host := request.Host
	if host == "" {
		host = request.URL.Host
	}
	headers := map[string]string{"host": host}
	for name, values := range request.Header {
		headers[strings.ToLower(name)] = strings.TrimSpace(strings.Join(values, ","))
	}
	names := make([]string, 0, len(headers))
	for name := range headers {
		names = append(names, name)
	}
	sort.Strings(names)
	var canonicalHeaders strings.Builder
	for _, name := range names {
		canonicalHeaders.WriteString(name + ":" + headers[name] + "\n")
	}
	signedHeaders := strings.Join(names, ";")
	path := request.URL.EscapedPath()
	if path == "" {
		path = "/"
	}
	canonicalRequest := strings.Join([]string{request.Method, path, canonicalQuery(request.URL.Query()),
		canonicalHeaders.String(), signedHeaders, emptyPayloadSHA256}, "\n")
	scope := day + "/" + config.Region + "/s3/aws4_request"
	stringToSign := "AWS4-HMAC-SHA256\n" + amzDate + "\n" + scope + "\n" + sha256Hex(canonicalRequest)
	key := hmacSHA256([]byte("AWS4"+config.SecretKey), day)
	key = hmacSHA256(key, config.Region)
	key = hmacSHA256(key, "s3")
	key = hmacSHA256(key, "aws4_request")
	signature := hex.EncodeToString(hmacSHA256(key, stringToSign))
	request.Header.Set("Authorization", "AWS4-HMAC-SHA256 Credential="+config.AccessKey+"/"+scope+
		", SignedHeaders="+signedHeaders+", Signature="+signature)
}

func canonicalQuery(values url.Values) string {
	parts := []string{}
	for name, list := range values {
		for _, value := range list {
			parts = append(parts, awsEscape(name)+"="+awsEscape(value))
		}
	}
	sort.Strings(parts)
	return strings.Join(parts, "&")
}

func awsEscape(value string) string {
	return strings.ReplaceAll(url.QueryEscape(value), "+", "%20")
}

func sha256Hex(value string) string {
	sum := sha256.Sum256([]byte(value))
	return hex.EncodeToString(sum[:])
}

func hmacSHA256(key []byte, value string) []byte {
	mac := hmac.New(sha256.New, key)
	mac.Write([]byte(value))
	return mac.Sum(nil)
}
