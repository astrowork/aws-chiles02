/**
 *  (c) UWA, The University of Western Australia
 *  M468/35 Stirling Hwy
 *  Perth WA 6009
 *  Australia
 *
 *  Copyright by UWA, 2016
 *  All rights reserved
 *
 *  This library is free software; you can redistribute it and/or
 *  modify it under the terms of the GNU Lesser General Public
 *  License as published by the Free Software Foundation; either
 *  version 2.1 of the License, or (at your option) any later version.
 *
 *  This library is distributed in the hope that it will be useful,
 *  but WITHOUT ANY WARRANTY; without even the implied warranty of
 *  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU
 *  Lesser General Public License for more details.
 *
 *  You should have received a copy of the GNU Lesser General Public
 *  License along with this library; if not, write to the Free Software
 *  Foundation, Inc., 59 Temple Place, Suite 330, Boston,
 *  MA 02111-1307  USA
 */
package org.icrar.awsChiles02.copyS3;

/**
 * Created by mboulton on 18/01/2016.
 *
 * A <code>MultiByteArrayInputStream</code> represents the logical
 * concatenation of many byte[]s into one <code>InputStream</code>.
 */


import java.io.ByteArrayInputStream;
import java.io.IOException;
import java.io.InputStream;
import java.util.ArrayDeque;
import java.util.Queue;

/**
 *
 */
public class MultiByteArrayInputStream extends InputStream {
    private static final class Lock { }
    private final Object lock = new Lock();
    private static final Queue<byte[]> inputStreams = new ArrayDeque<byte[]>();
    private ByteArrayInputStream ins;
    private boolean lastStreamAdded = false;
    private boolean mpdinsClosed = false;

    public MultiByteArrayInputStream() {

    }

    /**
     *  Continues reading in the next byte[] if an EOF is reached.
     */
    private final void nextStream() throws IOException {
        if (ins != null) {
            ins.close();
            ins = null;
        }

        synchronized (inputStreams) {
            if (!inputStreams.isEmpty()) {
                // This will get and remove the next stream.
                byte[] bytes = inputStreams.poll();
                if (bytes == null) {
                    throw new NullPointerException();
                }
                ins = new ByteArrayInputStream(bytes);
                if (ins == null) {
                    throw new NullPointerException();
                }
            } else {
                ins = null;
            }
        }
    }

    /**
     *
     * @param inputBytes
     * @throws IOException
     */
    public synchronized void addByteArray(byte[] inputBytes) throws IOException {
        if (lastStreamAdded || mpdinsClosed) {
            throw new IOException("Last stream already added or stream closed");
        }
        synchronized (inputStreams) {
            inputStreams.add(inputBytes);
            inputStreams.notifyAll();
        }
    }

    /**
     *
     * @param inputBytes
     * @throws IOException
     */
    public synchronized void addLastInputStream(byte[] inputBytes) throws IOException {
        if (lastStreamAdded || mpdinsClosed) {
            throw new IOException("Last stream already added or stream closed");
        }
        inputStreams.add(inputBytes);
        lastStreamAdded = true;
        inputStreams.notifyAll();
    }

    @Override
    public int available() throws IOException {
        if (ins == null) {
            return 0; // no way to signal EOF from available()
        }
        return ins.available();
    }

    private void waitForNextInputStream() throws IOException {
        if (mpdinsClosed) {
            throw new IOException("InputStream closed");
        }
        synchronized (inputStreams) {
            while (inputStreams.isEmpty()) {
                try {
                    inputStreams.wait();
                } catch (InterruptedException e) {
                    e.printStackTrace();
                }
            }
        }
        nextStream();
    }

    /**
     *
     */
    @Override
    public int read() throws IOException {
        if (ins == null) {
            if (lastStreamAdded || mpdinsClosed) {
                return -1;
            } else {
                waitForNextInputStream();
            }
        }
        int c = ins.read();
        if (c == -1) {
            nextStream();
            return read();
        }
        return c;
    }

    /**
     *
     */
    @Override
    public int read(byte b[], int off, int len) throws IOException {
        if (ins == null) {
            if (lastStreamAdded || mpdinsClosed) {
                return -1;
            } else {
                waitForNextInputStream();
            }
        } else if (b == null) {
            throw new NullPointerException();
        } else if (off < 0 || len < 0 || len > b.length - off) {
            throw new IndexOutOfBoundsException();
        } else if (len == 0) {
            return 0;
        }

        int n = ins.read(b, off, len);
        if (n <= 0) {
            nextStream();
            return read(b, off, len);
        }
        return n;
    }

    /**
     *
     */
    @Override
    public void close() throws IOException {
        do {
            nextStream();
        } while (ins != null);
        mpdinsClosed = true;
    }
}
