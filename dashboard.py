import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from scapy.all import *
from collections import defaultdict
import time
from datetime import datetime
import threading
import warnings
import logging
from typing import Dict, List, Optional
import socket
from geopy.geocoders import Nominatim
from scapy.layers.inet import IP, TCP, UDP

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Implement the functionality of processing our captured packets
class PacketProcessor:
    """Process and analyze network packets"""

    def __init__(self):
        self.protocol_map = {
            1: 'ICMP',
            6: 'TCP',
            17: 'UDP'
        }
        self.packet_data = []
        self.start_time = datetime.now()
        self.packet_count = 0
        self.lock = threading.Lock()
        self.geolocator = Nominatim(user_agent="network_dashboard")
        self.alerts = []

    def get_protocol_name(self, protocol_num: int) -> str:
        """Convert protocol number to name"""
        return self.protocol_map.get(protocol_num, f'OTHER({protocol_num})')

    def add_alert(self, condition: callable, message: str):
        """Add a custom alert"""
        self.alerts.append((condition, message))

    def check_alerts(self, packet_info: dict):
        """Check and trigger alerts"""
        for condition, message in self.alerts:
            if condition(packet_info):
                st.warning(f"Alert: {message}")

    def process_packet(self, packet) -> None:
        """Process a single packet and extract relevant information"""
        try:
            if IP in packet:
                with self.lock:
                    packet_info = {
                        'timestamp': datetime.now(),
                        'source': packet[IP].src,
                        'destination': packet[IP].dst,
                        'protocol': self.get_protocol_name(packet[IP].proto),
                        'size': len(packet),
                        'time_relative': (datetime.now() - self.start_time).total_seconds()
                    }

                    # Add TCP-specific information
                    if TCP in packet:
                        packet_info.update({
                            'src_port': packet[TCP].sport,
                            'dst_port': packet[TCP].dport,
                            'tcp_flags': packet[TCP].flags
                        })

                    # Add UDP-specific information
                    elif UDP in packet:
                        packet_info.update({
                            'src_port': packet[UDP].sport,
                            'dst_port': packet[UDP].dport
                        })

                    # Geographical mapping
                    try:
                        location = self.geolocator.geocode(packet[IP].src)
                        if location:
                            packet_info['latitude'] = location.latitude
                            packet_info['longitude'] = location.longitude
                    except Exception as e:
                        logger.error(f"Error in geolocation: {str(e)}")

                    # Check alerts
                    self.check_alerts(packet_info)

                    self.packet_data.append(packet_info)
                    self.packet_count += 1

                    # Keep only last 10000 packets to prevent memory issues
                    if len(self.packet_data) > 10000:
                        self.packet_data.pop(0)

        except Exception as e:
            logger.error(f"Error processing packet: {str(e)}")

    def get_dataframe(self) -> pd.DataFrame:
        """Convert packet data to pandas DataFrame"""
        with self.lock:
            return pd.DataFrame(self.packet_data)

# Create a Streamlit dashboard
def create_visualizations(df: pd.DataFrame):
    """Create all dashboard visualizations"""
    if len(df) > 0:
        # Protocol distribution
        protocol_counts = df['protocol'].value_counts()
        fig_protocol = px.pie(
            values=protocol_counts.values,
            names=protocol_counts.index,
            title="Protocol Distribution"
        )
        st.plotly_chart(fig_protocol, use_container_width=True)

        # Packets timeline
        df['timestamp'] = pd.to_datetime(df['timestamp'])
        df_grouped = df.groupby(df['timestamp'].dt.floor('S')).size()
        fig_timeline = px.line(
            x=df_grouped.index,
            y=df_grouped.values,
            title="Packets per Second"
        )
        st.plotly_chart(fig_timeline, use_container_width=True)

        # Top source IPs
        top_sources = df['source'].value_counts().head(10)
        fig_sources = px.bar(
            x=top_sources.index,
            y=top_sources.values,
            title="Top Source IP Addresses"
        )
        st.plotly_chart(fig_sources, use_container_width=True)

        # Geographical mapping
        if 'latitude' in df.columns and 'longitude' in df.columns:
            fig_map = px.scatter_mapbox(
                df,
                lat='latitude',
                lon='longitude',
                hover_name='source',
                hover_data=['destination', 'protocol', 'size'],
                title="Geographical IP Mapping",
                mapbox_style="carto-positron"
            )
            st.plotly_chart(fig_map, use_container_width=True)



# capture packets
def start_packet_capture():
    """Start packet capture in a separate thread"""
    processor = PacketProcessor()

    def capture_packets():
        sniff(prn=processor.process_packet, store=False)

    capture_thread = threading.Thread(target=capture_packets, daemon=True)
    capture_thread.start()

    return processor

# Main function
def main():
    """Main function to run the dashboard"""
    st.set_page_config(page_title="Network Traffic Analysis", layout="wide")
    st.title("Real-time Network Traffic Analysis")

    # Initialize packet processor in session state
    if 'processor' not in st.session_state:
        st.session_state.processor = start_packet_capture()
        st.session_state.start_time = time.time()

    # Create dashboard layout
    col1, col2 = st.columns(2)

    # Get current data
    df = st.session_state.processor.get_dataframe()

    # Display metrics
    with col1:
        st.metric("Total Packets", len(df))
    with col2:
        duration = time.time() - st.session_state.start_time
        st.metric("Capture Duration", f"{duration:.2f}s")

    # Display visualizations
    create_visualizations(df)

    # Display recent packets
    st.subheader("Recent Packets")
    if len(df) > 0:
        st.dataframe(
            df.tail(10)[['timestamp', 'source', 'destination', 'protocol', 'size']],
            use_container_width=True
        )

    # Add custom alerts
    st.session_state.processor.add_alert(
        lambda pkt: pkt['protocol'] == 'TCP' and pkt['size'] > 1000,
        "Large TCP packet detected"
    )

    # Add packet payload analysis option
    st.subheader("Packet Payload Analysis")
    if len(df) > 0:
        packet_index = st.number_input("Select packet index", min_value=0, max_value=len(df)-1, step=1)
        st.text_area("Payload", str(df.iloc[packet_index]))

    # Add hostname resolution
    st.subheader("Hostname Resolution")
    hostname = st.text_input("Enter hostname to resolve")
    if hostname:
        resolved_ip = resolve_hostname(hostname)
        if resolved_ip:
            st.write(f"Resolved IP: {resolved_ip}")
        else:
            st.write("Could not resolve hostname")

    # Add refresh button
    if st.button('Refresh Data'):
        st.rerun()

    # Auto refresh
    time.sleep(2)
    st.rerun()

if __name__ == "__main__":
    main()