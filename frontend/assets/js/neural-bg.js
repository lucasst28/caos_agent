/**
 * Neural Background - Animated particle system with Three.js
 * Creates a subtle, animated background for the CAOS dashboard
 */

(function () {
    'use strict';

    const NeuralBG = {
        scene: null,
        camera: null,
        renderer: null,
        particles: null,
        connections: null,
        animationId: null,
        mouseX: 0,
        mouseY: 0,

        init: function (containerId) {
            const container = document.querySelector(containerId);
            if (!container) return;

            // Scene setup
            this.scene = new THREE.Scene();

            // Camera
            this.camera = new THREE.PerspectiveCamera(
                75,
                window.innerWidth / window.innerHeight,
                0.1,
                1000
            );
            this.camera.position.z = 50;

            // Renderer
            this.renderer = new THREE.WebGLRenderer({
                alpha: true,
                antialias: true
            });
            this.renderer.setSize(window.innerWidth, window.innerHeight);
            this.renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
            this.renderer.setClearColor(0x000000, 0);
            container.appendChild(this.renderer.domElement);

            // Create particles
            this.createParticles();

            // Event listeners
            window.addEventListener('resize', this.onResize.bind(this));
            window.addEventListener('mousemove', this.onMouseMove.bind(this));

            // Start animation
            this.animate();
        },

        createParticles: function () {
            const particleCount = 150;
            const geometry = new THREE.BufferGeometry();
            const positions = new Float32Array(particleCount * 3);
            const colors = new Float32Array(particleCount * 3);
            const sizes = new Float32Array(particleCount);

            // CAOS color palette
            const palette = [
                { r: 1.0, g: 0.2, b: 0.4 },   // caos-red
                { r: 0.0, g: 0.76, b: 1.0 },  // atlas-blue
                { r: 0.0, g: 0.96, b: 0.71 }, // action-green
                { r: 0.3, g: 0.23, b: 1.0 },  // intel-purple
            ];

            for (let i = 0; i < particleCount; i++) {
                const i3 = i * 3;

                // Position - spread across a larger area
                positions[i3] = (Math.random() - 0.5) * 120;
                positions[i3 + 1] = (Math.random() - 0.5) * 80;
                positions[i3 + 2] = (Math.random() - 0.5) * 60;

                // Color - pick from palette
                const color = palette[Math.floor(Math.random() * palette.length)];
                colors[i3] = color.r;
                colors[i3 + 1] = color.g;
                colors[i3 + 2] = color.b;

                // Size
                sizes[i] = Math.random() * 2 + 0.5;
            }

            geometry.setAttribute('position', new THREE.BufferAttribute(positions, 3));
            geometry.setAttribute('color', new THREE.BufferAttribute(colors, 3));
            geometry.setAttribute('size', new THREE.BufferAttribute(sizes, 1));

            // Shader material for glowing particles
            const material = new THREE.ShaderMaterial({
                uniforms: {
                    time: { value: 0 },
                    pixelRatio: { value: window.devicePixelRatio }
                },
                vertexShader: `
                    attribute float size;
                    varying vec3 vColor;
                    uniform float time;
                    
                    void main() {
                        vColor = color;
                        
                        vec3 pos = position;
                        pos.y += sin(time * 0.5 + position.x * 0.1) * 2.0;
                        pos.x += cos(time * 0.3 + position.y * 0.1) * 1.5;
                        
                        vec4 mvPosition = modelViewMatrix * vec4(pos, 1.0);
                        gl_PointSize = size * (300.0 / -mvPosition.z);
                        gl_Position = projectionMatrix * mvPosition;
                    }
                `,
                fragmentShader: `
                    varying vec3 vColor;
                    
                    void main() {
                        float dist = length(gl_PointCoord - vec2(0.5));
                        if (dist > 0.5) discard;
                        
                        float alpha = 1.0 - smoothstep(0.0, 0.5, dist);
                        alpha *= 0.6;
                        
                        gl_FragColor = vec4(vColor, alpha);
                    }
                `,
                transparent: true,
                vertexColors: true,
                blending: THREE.AdditiveBlending,
                depthWrite: false
            });

            this.particles = new THREE.Points(geometry, material);
            this.scene.add(this.particles);

            // Create connection lines
            this.createConnections();
        },

        createConnections: function () {
            const lineGeometry = new THREE.BufferGeometry();
            const linePositions = new Float32Array(300 * 6); // 300 potential connections, 2 points each

            lineGeometry.setAttribute('position', new THREE.BufferAttribute(linePositions, 3));
            lineGeometry.setDrawRange(0, 0);

            const lineMaterial = new THREE.LineBasicMaterial({
                color: 0xff3366,
                transparent: true,
                opacity: 0.1,
                blending: THREE.AdditiveBlending
            });

            this.connections = new THREE.LineSegments(lineGeometry, lineMaterial);
            this.scene.add(this.connections);
        },

        updateConnections: function () {
            if (!this.particles || !this.connections) return;

            const positions = this.particles.geometry.attributes.position.array;
            const linePositions = this.connections.geometry.attributes.position.array;
            const particleCount = positions.length / 3;
            const maxDistance = 20;
            let lineIndex = 0;
            const maxConnections = 100;

            for (let i = 0; i < particleCount && lineIndex < maxConnections; i++) {
                for (let j = i + 1; j < particleCount && lineIndex < maxConnections; j++) {
                    const dx = positions[i * 3] - positions[j * 3];
                    const dy = positions[i * 3 + 1] - positions[j * 3 + 1];
                    const dz = positions[i * 3 + 2] - positions[j * 3 + 2];
                    const dist = Math.sqrt(dx * dx + dy * dy + dz * dz);

                    if (dist < maxDistance) {
                        linePositions[lineIndex * 6] = positions[i * 3];
                        linePositions[lineIndex * 6 + 1] = positions[i * 3 + 1];
                        linePositions[lineIndex * 6 + 2] = positions[i * 3 + 2];
                        linePositions[lineIndex * 6 + 3] = positions[j * 3];
                        linePositions[lineIndex * 6 + 4] = positions[j * 3 + 1];
                        linePositions[lineIndex * 6 + 5] = positions[j * 3 + 2];
                        lineIndex++;
                    }
                }
            }

            this.connections.geometry.setDrawRange(0, lineIndex * 2);
            this.connections.geometry.attributes.position.needsUpdate = true;
        },

        animate: function () {
            this.animationId = requestAnimationFrame(this.animate.bind(this));

            if (this.particles && this.particles.material.uniforms) {
                this.particles.material.uniforms.time.value += 0.01;
            }

            // Subtle camera movement based on mouse
            this.camera.position.x += (this.mouseX * 0.02 - this.camera.position.x) * 0.05;
            this.camera.position.y += (-this.mouseY * 0.02 - this.camera.position.y) * 0.05;
            this.camera.lookAt(this.scene.position);

            // Update connections occasionally (performance)
            if (Math.random() > 0.95) {
                this.updateConnections();
            }

            this.renderer.render(this.scene, this.camera);
        },

        onResize: function () {
            this.camera.aspect = window.innerWidth / window.innerHeight;
            this.camera.updateProjectionMatrix();
            this.renderer.setSize(window.innerWidth, window.innerHeight);
        },

        onMouseMove: function (event) {
            this.mouseX = (event.clientX - window.innerWidth / 2);
            this.mouseY = (event.clientY - window.innerHeight / 2);
        },

        destroy: function () {
            if (this.animationId) {
                cancelAnimationFrame(this.animationId);
            }
            window.removeEventListener('resize', this.onResize);
            window.removeEventListener('mousemove', this.onMouseMove);
        }
    };

    // Initialize when DOM is ready
    document.addEventListener('DOMContentLoaded', function () {
        NeuralBG.init('#canvas-container');
    });

    // Expose globally
    window.NeuralBG = NeuralBG;
})();
