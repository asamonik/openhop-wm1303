"""Shared queued transmission and fallback handling for Channels E and F."""

import time

from repeater.bridge_engine import _stable_hash
from repeater.web.packet_trace import trace_event


async def send_channel_packet(endpoint, data, *, channel, load_ui, default_tx_power, logger):
    """Send through the shared scheduler, retaining channel-specific TX settings.

    The backend emits the detailed radio trace; this helper records its final
    outcome, updates endpoint counters, and stores successful queued TX metrics.
    """
    label = channel.removeprefix('channel_').upper()
    pkt_hash8 = _stable_hash(data)[:8]
    try:
        friendly = endpoint.bridge._dn(channel) if endpoint.bridge else channel
    except Exception:
        friendly = channel

    def trace(detail, status):
        trace_event(pkt_hash8, 'tx_send', channel=channel, detail=detail, status=status)

    if endpoint.backend is None:
        endpoint.tx_errors += 1
        logger.warning('Channel %s TX: no backend, cannot send %d bytes', label, len(data))
        trace('TX FAILED on %s: no backend available' % friendly, 'error')
        return {'ok': False, 'error': 'no_backend'}

    try:
        manager = getattr(endpoint.backend, '_tx_queue_manager', None)
        queue = manager.queues.get(channel) if manager else None
        if queue:
            result = await queue.enqueue(data, trace_hash=pkt_hash8)
            if not result.get('ok'):
                endpoint.tx_errors += 1
                logger.warning('Channel %s TX FAIL: %s (%d bytes)',
                               label, result.get('error', 'unknown'), len(data))
                trace('TX FAILED on %s: %s' % (friendly, result.get('error', 'unknown')), 'error')
                return result

            endpoint.tx_packets += 1
            logger.info('Channel %s TX: sent %d bytes via HAL (send=%.1fms, airtime=%.1fms)',
                        label, len(data), result.get('send_ms', 0), result.get('airtime_ms', 0))
            try:
                detail = endpoint.bridge._format_tx_result(
                    result, friendly, 'Repeater → %s' % friendly)
            except Exception:
                detail = ('TX on %s\n  Send: %.1fms\n  Airtime: %.1fms\n  Queue wait: %.1fms' % (
                    friendly, result.get('send_ms', 0), result.get('airtime_ms', 0),
                    result.get('queue_wait_ms', 0)))
            trace(detail, 'ok')

            # Metrics are optional; a storage failure must not turn a sent
            # packet into a failed transmission.
            try:
                handler = getattr(endpoint.bridge, '_sqlite_handler', None) if endpoint.bridge else None
                if handler is not None:
                    handler.store_packet_metric({
                        'timestamp': time.time(),
                        'channel_id': channel,
                        'direction': 'tx',
                        'length': len(data),
                        'hop_count': None,
                        'crc_ok': True,
                        'airtime_ms': float(result.get('airtime_ms', 0) or 0),
                        'wait_time_ms': float(result.get('queue_wait_ms', 0) or 0),
                        'pkt_hash': pkt_hash8,
                    })
            except Exception as exc:
                logger.warning('packet_metric TX(ch_%s) store failed: %s', label.lower(), exc)
            return result

        if manager:
            logger.warning('Channel %s TX: no %s queue found in TXQueueManager', label, channel)
        else:
            logger.warning('Channel %s TX: no TXQueueManager on backend', label)

        tx_power = int(load_ui().get('tx_power', default_tx_power))
        result = await endpoint.backend.send(channel, data, tx_power=tx_power, trace_hash=pkt_hash8)
        if not isinstance(result, dict) or not result.get('ok'):
            endpoint.tx_errors += 1
            if not isinstance(result, dict):
                result = {'ok': False, 'error': 'invalid_send_result'}
            trace('TX FAILED on %s: %s' % (friendly, result.get('error', 'unknown')), 'error')
            return result

        endpoint.tx_packets += 1
        logger.info('Channel %s TX: sent %d bytes via backend.send() (tx_power=%d)',
                    label, len(data), tx_power)
        trace('TX on %s via backend.send() (tx_power=%d dBm, %d bytes)' % (
            friendly, tx_power, len(data)), 'ok')
        return result
    except Exception as exc:
        endpoint.tx_errors += 1
        logger.error('Channel %s TX error: %s', label, exc)
        trace('TX FAILED on %s: %s' % (friendly, exc), 'error')
        return {'ok': False, 'error': str(exc)}
