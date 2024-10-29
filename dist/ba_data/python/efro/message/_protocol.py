# Released under the MIT License. See LICENSE for details.
#
"""Functionality for sending and responding to messages.
Supports static typing for message types and possible return types.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
import traceback
import logging
import json

from efro.error import CleanError, RemoteError
from efro.dataclassio import (is_ioprepped_dataclass, dataclass_to_dict,
                              dataclass_from_dict)
from efro.message._message import (Message, Response, ErrorResponse,
                                   EmptyResponse, ErrorType,
                                   UnregisteredMessageIDError)

if TYPE_CHECKING:
    from typing import Any, Optional, Literal


class MessageProtocol:
    """Wrangles a set of message types, formats, and response types.
    Both endpoints must be using a compatible Protocol for communication
    to succeed. To maintain Protocol compatibility between revisions,
    all message types must retain the same id, message attr storage names must
    not change, newly added attrs must have default values, etc.
    """

    def __init__(self,
                 message_types: dict[int, type[Message]],
                 response_types: dict[int, type[Response]],
                 type_key: Optional[str] = None,
                 preserve_clean_errors: bool = True,
                 log_remote_exceptions: bool = True,
                 trusted_sender: bool = False) -> None:
        """Create a protocol with a given configuration.

        Note that common response types are automatically registered
        with (unchanging negative ids) so they don't need to be passed
        explicitly (but can be if a different id is desired).

        If 'type_key' is provided, the message type ID is stored as the
        provided key in the message dict; otherwise it will be stored as
        part of a top level dict with the message payload appearing as a
        child dict. This is mainly for backwards compatibility.

        If 'preserve_clean_errors' is True, efro.error.CleanError errors
        on the remote end will result in the same error raised locally.
        All other Exception types come across as efro.error.RemoteError.

        If 'trusted_sender' is True, stringified remote stack traces will
        be included in the responses if errors occur.
        """
        # pylint: disable=too-many-locals
        self.message_types_by_id: dict[int, type[Message]] = {}
        self.message_ids_by_type: dict[type[Message], int] = {}
        self.response_types_by_id: dict[int, type[Response]] = {}
        self.response_ids_by_type: dict[type[Response], int] = {}
        for m_id, m_type in message_types.items():

            # Make sure only valid message types were passed and each
            # id was assigned only once.
            assert isinstance(m_id, int)
            assert m_id >= 0
            assert (is_ioprepped_dataclass(m_type)
                    and issubclass(m_type, Message))
            assert self.message_types_by_id.get(m_id) is None
            self.message_types_by_id[m_id] = m_type
            self.message_ids_by_type[m_type] = m_id

        for r_id, r_type in response_types.items():
            assert isinstance(r_id, int)
            assert r_id >= 0
            assert (is_ioprepped_dataclass(r_type)
                    and issubclass(r_type, Response))
            assert self.response_types_by_id.get(r_id) is None
            self.response_types_by_id[r_id] = r_type
            self.response_ids_by_type[r_type] = r_id

        # Go ahead and auto-register a few common response types
        # if the user has not done so explicitly. Use unique negative
        # IDs which will never change or overlap with user ids.
        def _reg_if_not(reg_tp: type[Response], reg_id: int) -> None:
            if reg_tp in self.response_ids_by_type:
                return
            assert self.response_types_by_id.get(reg_id) is None
            self.response_types_by_id[reg_id] = reg_tp
            self.response_ids_by_type[reg_tp] = reg_id

        _reg_if_not(ErrorResponse, -1)
        _reg_if_not(EmptyResponse, -2)
        # _reg_if_not(BoolResponse, -3)

        # Some extra-thorough validation in debug mode.
        if __debug__:
            # Make sure all Message types' return types are valid
            # and have been assigned an ID as well.
            all_response_types: set[type[Response]] = set()
            for m_id, m_type in message_types.items():
                m_rtypes = m_type.get_response_types()
                assert isinstance(m_rtypes, list)
                assert m_rtypes, (
                    f'Message type {m_type} specifies no return types.')
                assert len(set(m_rtypes)) == len(m_rtypes)  # check dups
                all_response_types.update(m_rtypes)
            for cls in all_response_types:
                assert is_ioprepped_dataclass(cls)
                assert issubclass(cls, Response)
                if cls not in self.response_ids_by_type:
                    raise ValueError(f'Possible response type {cls}'
                                     f' needs to be included in response_types'
                                     f' for this protocol.')

            # Make sure all registered types have unique base names.
            # We can take advantage of this to generate cleaner looking
            # protocol modules. Can revisit if this is ever a problem.
            mtypenames = set(tp.__name__ for tp in self.message_ids_by_type)
            if len(mtypenames) != len(message_types):
                raise ValueError(
                    'message_types contains duplicate __name__s;'
                    ' all types are required to have unique names.')

        self._type_key = type_key
        self.preserve_clean_errors = preserve_clean_errors
        self.log_remote_exceptions = log_remote_exceptions
        self.trusted_sender = trusted_sender

    def encode_message(self, message: Message) -> str:
        """Encode a message to a json string for transport."""
        return self._encode(message, self.message_ids_by_type, 'message')

    def encode_response(self, response: Response) -> str:
        """Encode a response to a json string for transport."""
        return self._encode(response, self.response_ids_by_type, 'response')

    def encode_error_response(self, exc: Exception) -> str:
        """Return a raw response for an error that occurred during handling."""
        if self.log_remote_exceptions:
            logging.exception('Error handling message.')

        # If anything goes wrong, return a ErrorResponse instead.
        if isinstance(exc, CleanError) and self.preserve_clean_errors:
            err_response = ErrorResponse(error_message=str(exc),
                                         error_type=ErrorType.CLEAN)
        else:
            err_response = ErrorResponse(
                error_message=(traceback.format_exc() if self.trusted_sender
                               else 'An unknown error has occurred.'),
                error_type=ErrorType.OTHER)
        return self.encode_response(err_response)

    def _encode(self, message: Any, ids_by_type: dict[type, int],
                opname: str) -> str:
        """Encode a message to a json string for transport."""

        m_id: Optional[int] = ids_by_type.get(type(message))
        if m_id is None:
            raise TypeError(f'{opname} type is not registered in protocol:'
                            f' {type(message)}')
        msgdict = dataclass_to_dict(message)

        # Encode type as part of the message/response dict if desired
        # (for legacy compatibility).
        if self._type_key is not None:
            if self._type_key in msgdict:
                raise RuntimeError(f'Type-key {self._type_key}'
                                   f' found in msg of type {type(message)}')
            msgdict[self._type_key] = m_id
            out = msgdict
        else:
            out = {'m': msgdict, 't': m_id}
        return json.dumps(out, separators=(',', ':'))

    def decode_message(self, data: str) -> Message:
        """Decode a message from a json string."""
        out = self._decode(data, self.message_types_by_id, 'message')
        assert isinstance(out, Message)
        return out

    def decode_response(self, data: str) -> Optional[Response]:
        """Decode a response from a json string."""
        out = self._decode(data, self.response_types_by_id, 'response')
        assert isinstance(out, (Response, type(None)))
        return out

    # Weeeird; we get mypy errors returning dict[int, type] but
    # dict[int, typing.Type] or dict[int, type[Any]] works..
    def _decode(self, data: str, types_by_id: dict[int, type[Any]],
                opname: str) -> Any:
        """Decode a message from a json string."""
        msgfull = json.loads(data)
        assert isinstance(msgfull, dict)
        msgdict: Optional[dict]
        if self._type_key is not None:
            m_id = msgfull.pop(self._type_key)
            msgdict = msgfull
            assert isinstance(m_id, int)
        else:
            m_id = msgfull.get('t')
            msgdict = msgfull.get('m')
        assert isinstance(m_id, int)
        assert isinstance(msgdict, dict)

        # Decode this particular type.
        msgtype = types_by_id.get(m_id)
        if msgtype is None:
            raise UnregisteredMessageIDError(
                f'Got unregistered {opname} id of {m_id}.')
        out = dataclass_from_dict(msgtype, msgdict)

        # Special case: if we get EmptyResponse, we simply return None.
        if isinstance(out, EmptyResponse):
            return None

        # Special case: a remote error occurred. Raise a local Exception
        # instead of returning the message.
        if isinstance(out, ErrorResponse):
            assert opname == 'response'
            if (self.preserve_clean_errors
                    and out.error_type is ErrorType.CLEAN):
                raise CleanError(out.error_message)
            raise RemoteError(out.error_message)

        return out

    def _get_module_header(self,
                           part: Literal['sender', 'receiver'],
                           extra_import_code: Optional[str] = None) -> str:
        """Return common parts of generated modules."""
        # pylint: disable=too-many-locals, too-many-branches
        import textwrap
        tpimports: dict[str, list[str]] = {}
        imports: dict[str, list[str]] = {}

        single_message_type = len(self.message_ids_by_type) == 1

        msgtypes = list(self.message_ids_by_type)
        if part == 'sender':
            msgtypes.append(Message)
        for msgtype in msgtypes:
            tpimports.setdefault(msgtype.__module__,
                                 []).append(msgtype.__name__)
        rsptypes = list(self.response_ids_by_type)
        if part == 'sender':
            rsptypes.append(Response)
        for rsp_tp in rsptypes:
            # Skip these as they don't actually show up in code.
            if rsp_tp is EmptyResponse or rsp_tp is ErrorResponse:
                continue
            if (single_message_type and part == 'sender'
                    and rsp_tp is not Response):
                # We need to cast to the single supported response type
                # in this case so need response types at runtime.
                imports.setdefault(rsp_tp.__module__,
                                   []).append(rsp_tp.__name__)
            else:
                tpimports.setdefault(rsp_tp.__module__,
                                     []).append(rsp_tp.__name__)

        import_lines = ''
        tpimport_lines = ''

        for module, names in sorted(imports.items()):
            jnames = ', '.join(names)
            line = f'from {module} import {jnames}'
            if len(line) > 79:
                # Recreate in a wrapping-friendly form.
                line = f'from {module} import ({jnames})'
            import_lines += f'{line}\n'
        for module, names in sorted(tpimports.items()):
            jnames = ', '.join(names)
            line = f'from {module} import {jnames}'
            if len(line) > 75:  # Account for indent
                # Recreate in a wrapping-friendly form.
                line = f'from {module} import ({jnames})'
            tpimport_lines += f'{line}\n'

        if part == 'sender':
            import_lines += ('from efro.message import MessageSender,'
                             ' BoundMessageSender')
            tpimport_typing_extras = ''
        else:
            if single_message_type:
                import_lines += ('from efro.message import (MessageReceiver,'
                                 ' BoundMessageReceiver, Message, Response)')
            else:
                import_lines += ('from efro.message import MessageReceiver,'
                                 ' BoundMessageReceiver')
            tpimport_typing_extras = ', Awaitable'

        if extra_import_code is not None:
            import_lines += f'\n{extra_import_code}\n'

        ovld = ', overload' if not single_message_type else ''
        tpimport_lines = textwrap.indent(tpimport_lines, '    ')

        # We need Optional for sender-modules with multiple types
        baseimps = ['Union', 'Any']
        if part == 'sender' and len(msgtypes) > 1:
            baseimps.append('Optional')
        if part == 'receiver':
            baseimps.append('Callable')
        baseimps_s = ', '.join(baseimps)
        out = ('# Released under the MIT License. See LICENSE for details.\n'
               f'#\n'
               f'"""Auto-generated {part} module. Do not edit by hand."""\n'
               f'\n'
               f'from __future__ import annotations\n'
               f'\n'
               f'from typing import TYPE_CHECKING{ovld}\n'
               f'\n'
               f'{import_lines}\n'
               f'\n'
               f'if TYPE_CHECKING:\n'
               f'    from typing import {baseimps_s}'
               f'{tpimport_typing_extras}\n'
               f'{tpimport_lines}'
               f'\n'
               f'\n')
        return out

    def do_create_sender_module(
            self,
            basename: str,
            protocol_create_code: str,
            enable_sync_sends: bool,
            enable_async_sends: bool,
            private: bool = False,
            protocol_module_level_import_code: Optional[str] = None) -> str:
        """Used by create_sender_module(); do not call directly."""
        # pylint: disable=too-many-locals
        import textwrap

        msgtypes = list(self.message_ids_by_type.keys())

        ppre = '_' if private else ''
        out = self._get_module_header(
            'sender', extra_import_code=protocol_module_level_import_code)
        ccind = textwrap.indent(protocol_create_code, '        ')
        out += (f'class {ppre}{basename}(MessageSender):\n'
                f'    """Protocol-specific sender."""\n'
                f'\n'
                f'    def __init__(self) -> None:\n'
                f'{ccind}\n'
                f'        super().__init__(protocol)\n'
                f'\n'
                f'    def __get__(self,\n'
                f'                obj: Any,\n'
                f'                type_in: Any = None)'
                f' -> {ppre}Bound{basename}:\n'
                f'        return {ppre}Bound{basename}'
                f'(obj, self)\n'
                f'\n'
                f'\n'
                f'class {ppre}Bound{basename}(BoundMessageSender):\n'
                f'    """Protocol-specific bound sender."""\n')

        def _filt_tp_name(rtype: type[Response]) -> str:
            # We accept None to equal EmptyResponse so reflect that
            # in the type annotation.
            return 'None' if rtype is EmptyResponse else rtype.__name__

        # Define handler() overloads for all registered message types.
        if msgtypes:
            for async_pass in False, True:
                if async_pass and not enable_async_sends:
                    continue
                if not async_pass and not enable_sync_sends:
                    continue
                pfx = 'async ' if async_pass else ''
                sfx = '_async' if async_pass else ''
                awt = 'await ' if async_pass else ''
                how = 'asynchronously' if async_pass else 'synchronously'

                if len(msgtypes) == 1:
                    # Special case: with a single message types we don't
                    # use overloads.
                    msgtype = msgtypes[0]
                    msgtypevar = msgtype.__name__
                    rtypes = msgtype.get_response_types()
                    if len(rtypes) > 1:
                        tps = ', '.join(_filt_tp_name(t) for t in rtypes)
                        rtypevar = f'Union[{tps}]'
                    else:
                        rtypevar = _filt_tp_name(rtypes[0])
                    out += (f'\n'
                            f'    {pfx}def send{sfx}(self,'
                            f' message: {msgtypevar})'
                            f' -> {rtypevar}:\n'
                            f'        """Send a message {how}."""\n'
                            f'        out = {awt}self._sender.'
                            f'send{sfx}(self._obj, message)\n'
                            f'        assert isinstance(out, {rtypevar})\n'
                            f'        return out\n')
                else:

                    for msgtype in msgtypes:
                        msgtypevar = msgtype.__name__
                        rtypes = msgtype.get_response_types()
                        if len(rtypes) > 1:
                            tps = ', '.join(_filt_tp_name(t) for t in rtypes)
                            rtypevar = f'Union[{tps}]'
                        else:
                            rtypevar = _filt_tp_name(rtypes[0])
                        out += (f'\n'
                                f'    @overload\n'
                                f'    {pfx}def send{sfx}(self,'
                                f' message: {msgtypevar})'
                                f' -> {rtypevar}:\n'
                                f'        ...\n')
                    out += (f'\n'
                            f'    {pfx}def send{sfx}(self, message: Message)'
                            f' -> Optional[Response]:\n'
                            f'        """Send a message {how}."""\n'
                            f'        return {awt}self._sender.'
                            f'send{sfx}(self._obj, message)\n')

        return out

    def do_create_receiver_module(
            self,
            basename: str,
            protocol_create_code: str,
            is_async: bool,
            private: bool = False,
            protocol_module_level_import_code: Optional[str] = None) -> str:
        """Used by create_receiver_module(); do not call directly."""
        # pylint: disable=too-many-locals
        import textwrap

        desc = 'asynchronous' if is_async else 'synchronous'
        ppre = '_' if private else ''
        msgtypes = list(self.message_ids_by_type.keys())
        out = self._get_module_header(
            'receiver', extra_import_code=protocol_module_level_import_code)
        ccind = textwrap.indent(protocol_create_code, '        ')
        out += (f'class {ppre}{basename}(MessageReceiver):\n'
                f'    """Protocol-specific {desc} receiver."""\n'
                f'\n'
                f'    is_async = {is_async}\n'
                f'\n'
                f'    def __init__(self) -> None:\n'
                f'{ccind}\n'
                f'        super().__init__(protocol)\n'
                f'\n'
                f'    def __get__(\n'
                f'        self,\n'
                f'        obj: Any,\n'
                f'        type_in: Any = None,\n'
                f'    ) -> {ppre}Bound{basename}:\n'
                f'        return {ppre}Bound{basename}('
                f'obj, self)\n')

        # Define handler() overloads for all registered message types.

        def _filt_tp_name(rtype: type[Response]) -> str:
            # We accept None to equal EmptyResponse so reflect that
            # in the type annotation.
            return 'None' if rtype is EmptyResponse else rtype.__name__

        if msgtypes:
            cbgn = 'Awaitable[' if is_async else ''
            cend = ']' if is_async else ''
            if len(msgtypes) == 1:
                # Special case: when we have a single message type we don't
                # use overloads.
                msgtype = msgtypes[0]
                msgtypevar = msgtype.__name__
                rtypes = msgtype.get_response_types()
                if len(rtypes) > 1:
                    tps = ', '.join(_filt_tp_name(t) for t in rtypes)
                    rtypevar = f'Union[{tps}]'
                else:
                    rtypevar = _filt_tp_name(rtypes[0])
                rtypevar = f'{cbgn}{rtypevar}{cend}'
                out += (
                    f'\n'
                    f'    def handler(\n'
                    f'        self,\n'
                    f'        call: Callable[[Any, {msgtypevar}], '
                    f'{rtypevar}],\n'
                    f'    )'
                    f' -> Callable[[Any, {msgtypevar}], {rtypevar}]:\n'
                    f'        """Decorator to register message handlers."""\n'
                    f'        from typing import cast, Callable, Any\n'
                    f'        self.register_handler(cast(Callable'
                    f'[[Any, Message], Response], call))\n'
                    f'        return call\n')
            else:
                for msgtype in msgtypes:
                    msgtypevar = msgtype.__name__
                    rtypes = msgtype.get_response_types()
                    if len(rtypes) > 1:
                        tps = ', '.join(_filt_tp_name(t) for t in rtypes)
                        rtypevar = f'Union[{tps}]'
                    else:
                        rtypevar = _filt_tp_name(rtypes[0])
                    rtypevar = f'{cbgn}{rtypevar}{cend}'
                    out += (f'\n'
                            f'    @overload\n'
                            f'    def handler(\n'
                            f'        self,\n'
                            f'        call: Callable[[Any, {msgtypevar}], '
                            f'{rtypevar}],\n'
                            f'    )'
                            f' -> Callable[[Any, {msgtypevar}], {rtypevar}]:\n'
                            f'        ...\n')
                out += (
                    '\n'
                    '    def handler(self, call: Callable) -> Callable:\n'
                    '        """Decorator to register message handlers."""\n'
                    '        self.register_handler(call)\n'
                    '        return call\n')

        out += (f'\n'
                f'\n'
                f'class {ppre}Bound{basename}(BoundMessageReceiver):\n'
                f'    """Protocol-specific bound receiver."""\n')
        if is_async:
            out += (
                '\n'
                '    async def handle_raw_message(self,\n'
                '                                 message: str,\n'
                '                                 raise_unregistered: bool ='
                ' False) -> str:\n'
                '        """Asynchronously handle a raw incoming message."""\n'
                '        return await self._receiver.handle_raw_message_async('
                '\n'
                '            self._obj, message, raise_unregistered)\n')

        else:
            out += (
                '\n'
                '    def handle_raw_message(self,\n'
                '                           message: str,\n'
                '                           raise_unregistered: bool = False)'
                ' -> str:\n'
                '        """Synchronously handle a raw incoming message."""\n'
                '        return self._receiver.handle_raw_message('
                'self._obj, message,\n'
                '                                                 '
                'raise_unregistered)\n')

        return out
