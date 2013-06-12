from base64 import b64encode
import datetime
import json
from django.conf import settings
from django.http import HttpResponse, HttpResponseBadRequest, HttpResponseNotFound, HttpResponseNotAllowed
import gripcontrol
import grip
import redis_ops

db = redis_ops.RedisOps()
pub = grip.Publisher()

if hasattr(settings, 'REDIS_HOST'):
	db.host = settings.REDIS_HOST

if hasattr(settings, 'REDIS_PORT'):
	db.port = settings.REDIS_PORT

if hasattr(settings, 'REDIS_DB'):
	db.db = settings.REDIS_DB

if hasattr(settings, 'GRIP_PROXIES'):
	grip_proxies = settings.GRIP_PROXIES
else:
	grip_proxies = list()

if hasattr(settings, 'WHINBOX_REDIS_PREFIX'):
	db.prefix = settings.WHINBOX_REDIS_PREFIX
else:
	db.prefix = 'wi-'

if hasattr(settings, 'WHINBOX_GRIP_PREFIX'):
	grip_prefix = settings.WHINBOX_GRIP_PREFIX
else:
	grip_prefix = 'wi-'

if hasattr(settings, 'WHINBOX_ITEM_MAX'):
	db.item_max = settings.WHINBOX_ITEM_MAX

if hasattr(settings, 'WHINBOX_ITEM_BURST_TIME'):
	db.item_burst_time = settings.WHINBOX_ITEM_BURST_TIME

if hasattr(settings, 'WHINBOX_ITEM_BURST_MAX'):
	db.item_burst_max = settings.WHINBOX_ITEM_BURST_MAX

pub.proxies = grip_proxies

# useful list derived from requestbin
ignore_headers = """
X-Varnish
X-Forwarded-For
X-Heroku-Dynos-In-Use
X-Request-Start
X-Heroku-Queue-Wait-Time
X-Heroku-Queue-Depth
X-Real-Ip
X-Forwarded-Proto
X-Via
X-Forwarded-Port
Grip-Sig
""".split("\n")[1:-1]

def _ignore_header(name):
	name = name.lower()
	for h in ignore_headers:
		if name == h.lower():
			return True
	return False

def _convert_header_name(name):
	out = ''
	word_start = True
	for c in name:
		if c == '_':
			out += '-'
			word_start = True
		elif word_start:
			out += c.upper()
			word_start = False
		else:
			out += c.lower()
	return out

def _req_to_item(req):
	item = dict()
	item['method'] = req.method
	item['path'] = req.path
	query = req.META.get('QUERY_STRING')
	if query:
		item['query'] = query
	raw_headers = list()
	content_length = req.META.get('CONTENT_LENGTH')
	if content_length:
		raw_headers.append(('CONTENT_LENGTH', content_length))
	content_type = req.META.get('CONTENT_TYPE')
	if content_type:
		raw_headers.append(('CONTENT_TYPE', content_type))
	for k, v in req.META.iteritems():
		if k.startswith('HTTP_'):
			raw_headers.append((k[5:], v))
	headers = list()
	for h in raw_headers:
		name = _convert_header_name(h[0])
		if not _ignore_header(name):
			headers.append([name, h[1]])
	item['headers'] = headers
	if len(req.raw_post_data) > 0:
		try:
			# if the body is valid utf-8, then store as text
			body = req.raw_post_data.decode('utf-8')
			item['body'] = body
		except:
			# else, store as binary
			item['body-bin'] = b64encode(req.raw_post_data)
	forwardedfor = req.META.get('HTTP_X_FORWARDED_FOR')
	if forwardedfor:
		ip_address = forwardedfor.split(',')[0].strip()
	else:
		ip_address = req.META['REMOTE_ADDR']
	item['ip_address'] = ip_address
	item['created'] = datetime.datetime.utcnow().isoformat()
	return item

def root(req):
	return HttpResponseNotFound('Not Found\n')

def create(req):
	if req.method == 'POST':
		host = req.META.get('HTTP_HOST')
		if not host:
			return HttpResponseBadRequest('Bad Request: No \'Host\' header\n')

		ttl = req.POST.get('ttl')
		if ttl is not None:
			ttl = int(ttl)
		if ttl is None:
			ttl = 3600

		try:
			inbox_id = db.inbox_create(ttl)
		except:
			return HttpResponse('Service Unavailable\n', status=503)

		out = dict()
		out['id'] = inbox_id
		out['base_url'] = 'http://' + host + '/i/' + inbox_id + '/'
		out['ttl'] = ttl
		return HttpResponse(json.dumps(out) + '\n', content_type='application/json')
	else:
		return HttpResponseNotAllowed(['POST'])

def inbox(req, inbox_id):
	if req.method == 'GET':
		host = req.META.get('HTTP_HOST')
		if not host:
			return HttpResponseBadRequest('Bad Request: No \'Host\' header\n')

		try:
			inbox = db.inbox_get(inbox_id)
		except redis_ops.InvalidId:
			return HttpResponseBadRequest('Bad Request: Invalid id\n')
		except redis_ops.ObjectDoesNotExist:
			return HttpResponseNotFound('Not Found\n')
		except:
			return HttpResponse('Service Unavailable\n', status=503)

		out = dict()
		out['id'] = inbox_id
		out['base_url'] = 'http://' + host + '/i/' + inbox_id + '/'
		out['ttl'] = inbox['ttl']
		return HttpResponse(json.dumps(out) + '\n', content_type='application/json')
	elif req.method == 'DELETE':
		try:
			db.inbox_delete(inbox_id)
		except redis_ops.InvalidId:
			return HttpResponseBadRequest('Bad Request: Invalid id\n')
		except redis_ops.ObjectDoesNotExist:
			return HttpResponseNotFound('Not Found\n')
		except:
			return HttpResponse('Service Unavailable\n', status=503)

		return HttpResponse('Deleted\n')
	else:
		return HttpResponseNotAllowed(['GET', 'DELETE'])

def refresh(req, inbox_id):
	if req.method == 'POST':
		ttl = req.POST.get('ttl')
		if ttl is not None:
			ttl = int(ttl)

		try:
			db.inbox_refresh(inbox_id, ttl)
		except redis_ops.InvalidId:
			return HttpResponseBadRequest('Bad Request: Invalid id\n')
		except redis_ops.ObjectDoesNotExist:
			return HttpResponseNotFound('Not Found\n')
		except:
			return HttpResponse('Service Unavailable\n', status=503)

		return HttpResponse('Refreshed\n')
	else:
		return HttpResponseNotAllowed(['POST'])

def hit(req, inbox_id):
	try:
		db.inbox_get(inbox_id)
	except redis_ops.InvalidId:
		return HttpResponseBadRequest('Bad Request: Invalid id\n')
	except redis_ops.ObjectDoesNotExist:
		return HttpResponseNotFound('Not Found\n')
	except:
		return HttpResponse('Service Unavailable\n', status=503)

	# pubsubhubbub verify request?
	hub_challenge = req.GET.get('hub.challenge')

	item = _req_to_item(req)
	if hub_challenge:
		item['type'] = 'hub-verify'
	else:
		item['type'] = 'normal'

	try:
		item_id, prev_id = db.inbox_append_item(inbox_id, item)
		db.inbox_clear_expired_items(inbox_id)
	except redis_ops.InvalidId:
		return HttpResponseBadRequest('Bad Request: Invalid id\n')
	except redis_ops.ObjectDoesNotExist:
		return HttpResponseNotFound('Not Found\n')
	except:
		return HttpResponse('Service Unavailable\n', status=503)

	item['id'] = item_id

	hr_headers = dict()
	hr_headers['Content-Type'] = 'application/json'
	hr = dict()
	hr['last_cursor'] = item_id
	hr['items'] = [item]
	hr_body = json.dumps(hr) + '\n'
	hs_body = json.dumps(item) + '\n'

	pub.publish(grip_prefix + 'inbox-' + inbox_id, item_id, prev_id, hr_headers, hr_body, hs_body)

	if hub_challenge:
		return HttpResponse(hub_challenge)
	else:
		return HttpResponse('Ok\n')

def items(req, inbox_id):
	if req.method == 'GET':
		try:
			db.inbox_refresh(inbox_id)
		except redis_ops.InvalidId:
			return HttpResponseBadRequest('Bad Request: Invalid id\n')
		except redis_ops.ObjectDoesNotExist:
			return HttpResponseNotFound('Not Found\n')
		except:
			return HttpResponse('Service Unavailable\n', status=503)

		order = req.GET.get('order')
		if order and order not in ('created', '-created'):
			return HttpResponseBadRequest('Bad Request: Invalid order value\n')

		if not order:
			order = 'created'

		imax = req.GET.get('max')
		if imax:
			try:
				imax = int(imax)
				if imax < 1:
					raise ValueError('max too small')
			except:
				return HttpResponseBadRequest('Bad Request: Invalid max value\n')

		if not imax or imax > 50:
			imax = 50

		since = req.GET.get('since')
		since_id = None
		since_cursor = None
		if since:
			if since.startswith('id:'):
				since_id = since[3:]
			elif since.startswith('cursor:'):
				since_cursor = since[7:]
			else:
				return HttpResponseBadRequest('Bad Request: Invalid since value\n')

		# at the moment, cursor is identical to id
		item_id = None
		if since_id:
			item_id = since_id
		elif since_cursor:
			item_id = since_cursor

		if order == 'created':
			try:
				items, last_id = db.inbox_get_items_after(inbox_id, item_id, imax)
			except redis_ops.InvalidId:
				return HttpResponseBadRequest('Bad Request: Invalid id\n')
			except redis_ops.ObjectDoesNotExist:
				return HttpResponseNotFound('Not Found\n')
			except:
				return HttpResponse('Service Unavailable\n', status=503)

			if len(items) > 0:
				out = dict()
				out['last_cursor'] = last_id
				out['items'] = items
				return HttpResponse(json.dumps(out) + '\n', content_type='application/json')

			if not grip.is_proxied(req, grip_proxies):
				return HttpResponse('Not Implemented\n', status=501)

			channel = gripcontrol.Channel(grip_prefix + 'inbox-' + inbox_id, last_id)
			theaders = dict()
			theaders['Content-Type'] = 'application/json'
			tbody = dict()
			tbody['last_cursor'] = last_id
			tbody['items'] = list()
			tbody_raw = json.dumps(tbody) + '\n'
			tresponse = gripcontrol.Response(headers=theaders, body=tbody_raw)
			instruct = gripcontrol.create_hold_response(channel, tresponse)
			return HttpResponse(instruct, content_type='application/grip-instruct')
		else: # -created
			try:
				items, last_id, eof = db.inbox_get_items_before(inbox_id, item_id, imax)
			except redis_ops.InvalidId:
				return HttpResponseBadRequest('Bad Request: Invalid id\n')
			except redis_ops.ObjectDoesNotExist:
				return HttpResponseNotFound('Not Found\n')
			except:
				return HttpResponse('Service Unavailable\n', status=503)

			out = dict()
			if not eof and last_id:
				out['last_cursor'] = last_id
			out['items'] = items
			return HttpResponse(json.dumps(out) + '\n', content_type='application/json')
	else:
		return HttpResponseNotAllowed(['GET'])

def stream(req, inbox_id):
	if req.method == 'GET':
		try:
			db.inbox_get(inbox_id)
		except redis_ops.InvalidId:
			return HttpResponseBadRequest('Bad Request: Invalid id\n')
		except redis_ops.ObjectDoesNotExist:
			return HttpResponseNotFound('Not Found\n')
		except:
			return HttpResponse('Service Unavailable\n', status=503)

		if not grip.is_proxied(req, grip_proxies):
			return HttpResponse('Not Implemented\n', status=501)

		rheaders = dict()
		rheaders['Content-Type'] = 'text/plain'
		response = gripcontrol.Response(headers=rheaders, body='[opened]\n')

		instruct = gripcontrol.create_hold_stream(grip_prefix + 'inbox-' + inbox_id, response)
		return HttpResponse(instruct, content_type='application/grip-instruct')
	else:
		return HttpResponseNotAllowed(['GET'])
