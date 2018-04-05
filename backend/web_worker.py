import functools
import json
import os
import time
import tornado.escape
import tornado.gen
import tornado.httpclient
import tornado.ioloop
import tornado.web
import tornado.websocket

import config
import chat_worker

ioloop = None
_oauth_route = None


@functools.lru_cache(maxsize=128)
@tornado.gen.coroutine
def _fetch_user(token, _):
    token = token.decode("utf-8")

    http_client = tornado.httpclient.AsyncHTTPClient()
    route = "https://api.stackexchange.com/2.2/me?key={}&access_token={}&site=stackoverflow".format(config.key, token)

    response = yield tornado.gen.Task(http_client.fetch, route)

    parsed = json.loads(response.body.decode("utf-8"))

    try:
        return parsed["items"][0]
    except:
        return


class FetchUser(tornado.web.RequestHandler):
    @tornado.gen.coroutine
    def prepare(self):
        token = self.get_secure_cookie("access_token")

        if token:
            self.current_user = yield _fetch_user(token, time.time() // 5400)

            if not self.current_user or self.current_user["reputation"] < 20:
                self.set_status(200)
                self.finish("<html><body>Sorry, you need at least 20 reputation to chat.</body></html>")


class GrillWS(tornado.websocket.WebSocketHandler):
    _sockets = set()
    _dev_sockets = set()

    @classmethod
    def broadcast(cls, msg):
        for socket in cls._sockets:
            socket.write_message(msg)

    @tornado.gen.coroutine
    def open(self):
        token = self.get_secure_cookie("access_token")

        if not token:
            self.close()
            return

        user = yield _fetch_user(token, time.time() // 5400)

        if not user or user["reputation"] < 20:
            self.close()
            return

        self.__class__._sockets.add(self)

        if user["account_id"] in config.devs:
            self._dev = True
            self._name = user["display_name"]

            self.__class__._dev_sockets.add(self)

    def on_message(self, message):
        if self._dev:
            message = "m{}: {}\n".format(self._name, message)

            for socket in self.__class__._dev_sockets:
                socket.write_message(message)


    def on_connection_close(self):
        self.on_finish()


    def on_finish(self):
        try:
            self.__class__.sockets.remove(self)
            self.__class__._dev_sockets.remove(self)
        except:
            pass


class OAuthHandler(tornado.web.RequestHandler):
    @tornado.gen.coroutine
    def get(self):
        code = self.get_query_argument("code")

        if code:
            http_client = tornado.httpclient.AsyncHTTPClient()
            body = "client_id={}&client_secret={}&code={}&redirect_uri={}" \
                    .format(config.client_id, os.environ["SESECRET"], tornado.escape.url_escape(code), config.redirect_uri)
            request = tornado.httpclient.HTTPRequest("https://stackexchange.com/oauth/access_token", method="POST", body=body)

            response = yield tornado.gen.Task(http_client.fetch, request)
            access_token = response.body.decode("utf-8").split("&")[0][13:]

            if access_token:
                self.set_secure_cookie("access_token", access_token, expires=86400)
                self.redirect("/")
                return

        self.set_status(400)


class EnqueueHandler(FetchUser):
    def put(self):
        if chat_worker.wrap_up:
            self.set_status(404)
        elif not self.current_user:
            self.set_status(403)
        else:
            account_id = self.current_user["account_id"]

            if account_id in chat_worker.waiting_users:
                self.set_status(204)
            else:
                name = tornado.escape.xhtml_escape(self.current_user["display_name"])
                chat_worker.waiting_users[account_id] = name

                response = "{}\x01{}".format(account_id, name)

                self.write(response)
                GrillWS.broadcast("q" + response)

                chat_worker.new_question.set()


class SkipStateHandler(FetchUser):
    def delete(self):
        if self.current_user and self.current_user["account_id"] in config.devs:
            chat_worker.skip_state.set()

            self.set_status(200)
        else:
            self.set_status(403)


class WrapUpHandler(FetchUser):
    def delete(self):
        if self.current_user and self.current_user["account_id"] in config.devs:
            chat_worker.wrap_up = True
            chat_worker.new_question.set()

            self.set_status(200)
            GrillWS.broadcast("w")
        else:
            self.set_status(403)


class HomeHandler(FetchUser):
    def get(self):
        self.set_header("cache-control", "no-store,no-cache,must-revalidate,max-age=0")

        if not self.current_user:
            self.redirect(_oauth_route)
        else:
            is_dev = self.current_user["account_id"] in config.devs

            self.render("../frontend/index.html", is_dev=is_dev, 
                                                  wrap_up=chat_worker.wrap_up, 
                                                  state=chat_worker.state.name, 
                                                  queue=chat_worker.waiting_users)


def start():
    global ioloop
    global _oauth_route

    _oauth_route = "https://stackexchange.com/oauth?client_id={}&redirect_uri={}" \
                    .format(config.client_id, config.redirect_uri)

    application = tornado.web.Application([
            (r"/", HomeHandler),
            (r"/ws", GrillWS),
            (r"/oauth_redirect", OAuthHandler),
            (r"/enqueue", EnqueueHandler),
            (r"/skip", SkipStateHandler),
            (r"/wrapup", WrapUpHandler)
    ], websocket_ping_interval=10, cookie_secret=os.environ["COOKIEKEY"], static_path="frontend")

    application.listen(os.environ["PORT"])

    ioloop = tornado.ioloop.IOLoop.current()
    ioloop.start()
