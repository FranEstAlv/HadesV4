-- Base de datos para Leviatan CHK API
-- Versión: 1.0

-- Crear base de datos
CREATE DATABASE IF NOT EXISTS leviatan_chk;
USE leviatan_chk;

-- Tabla de usuarios
CREATE TABLE IF NOT EXISTS users (
    id INT AUTO_INCREMENT PRIMARY KEY,
    username VARCHAR(100) UNIQUE NOT NULL,
    email VARCHAR(100) UNIQUE NOT NULL,
    password_hash VARCHAR(255) NOT NULL,
    api_key VARCHAR(255) UNIQUE,
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    INDEX idx_username (username),
    INDEX idx_email (email),
    INDEX idx_api_key (api_key)
);

-- Tabla de tarjetas verificadas
CREATE TABLE IF NOT EXISTS cards_checked (
    id INT AUTO_INCREMENT PRIMARY KEY,
    user_id INT,
    card_number VARCHAR(50),
    card_status ENUM('✅ Approved', '❌ Declined', '⚠️ Error') DEFAULT '⚠️ Error',
    response_message TEXT,
    wallet_status VARCHAR(50),
    transaction_id VARCHAR(100),
    amazon_response TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
    INDEX idx_status (card_status),
    INDEX idx_created (created_at),
    INDEX idx_user (user_id)
);

-- Tabla de sesiones de Amazon
CREATE TABLE IF NOT EXISTS amazon_sessions (
    id INT AUTO_INCREMENT PRIMARY KEY,
    user_id INT,
    cookies_encrypted TEXT,
    is_valid BOOLEAN DEFAULT FALSE,
    last_checked TIMESTAMP,
    expires_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
    INDEX idx_valid (is_valid),
    INDEX idx_expires (expires_at),
    INDEX idx_user (user_id)
);

-- Tabla de logs de API
CREATE TABLE IF NOT EXISTS api_logs (
    id INT AUTO_INCREMENT PRIMARY KEY,
    user_id INT,
    endpoint VARCHAR(255),
    method VARCHAR(10),
    status_code INT,
    request_data JSON,
    response_data JSON,
    ip_address VARCHAR(45),
    user_agent TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE SET NULL,
    INDEX idx_endpoint (endpoint),
    INDEX idx_created (created_at),
    INDEX idx_user (user_id)
);

-- Tabla de estadísticas
CREATE TABLE IF NOT EXISTS statistics (
    id INT AUTO_INCREMENT PRIMARY KEY,
    date DATE,
    total_checks INT DEFAULT 0,
    approved_count INT DEFAULT 0,
    declined_count INT DEFAULT 0,
    error_count INT DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY unique_date (date),
    INDEX idx_date (date)
);

-- Tabla de configuración
CREATE TABLE IF NOT EXISTS config (
    id INT AUTO_INCREMENT PRIMARY KEY,
    config_key VARCHAR(100) UNIQUE NOT NULL,
    config_value TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    INDEX idx_key (config_key)
);

-- Insertar configuración inicial
INSERT INTO config (config_key, config_value) VALUES
('api_version', '1.0'),
('api_name', 'Leviatan CHK'),
('max_requests_per_hour', '100'),
('enable_proxy_check', 'true'),
('enable_logging', 'true')
ON DUPLICATE KEY UPDATE config_value = VALUES(config_value);

-- Crear índices adicionales para optimización
CREATE INDEX idx_cards_created_status ON cards_checked(created_at, card_status);
CREATE INDEX idx_sessions_user_valid ON amazon_sessions(user_id, is_valid);
CREATE INDEX idx_logs_date_user ON api_logs(created_at, user_id);

-- Crear vista de estadísticas diarias
CREATE OR REPLACE VIEW daily_statistics AS
SELECT 
    DATE(created_at) as check_date,
    COUNT(*) as total_checks,
    SUM(CASE WHEN card_status = '✅ Approved' THEN 1 ELSE 0 END) as approved,
    SUM(CASE WHEN card_status = '❌ Declined' THEN 1 ELSE 0 END) as declined,
    SUM(CASE WHEN card_status = '⚠️ Error' THEN 1 ELSE 0 END) as errors
FROM cards_checked
GROUP BY DATE(created_at)
ORDER BY check_date DESC;

-- Crear vista de usuarios activos
CREATE OR REPLACE VIEW active_users AS
SELECT 
    u.id,
    u.username,
    u.email,
    COUNT(cc.id) as total_checks,
    SUM(CASE WHEN cc.card_status = '✅ Approved' THEN 1 ELSE 0 END) as approved_checks,
    u.created_at
FROM users u
LEFT JOIN cards_checked cc ON u.id = cc.user_id
WHERE u.is_active = TRUE
GROUP BY u.id, u.username, u.email, u.created_at;

-- Crear procedimiento para limpiar logs antiguos
DELIMITER //
CREATE PROCEDURE IF NOT EXISTS cleanup_old_logs()
BEGIN
    DELETE FROM api_logs 
    WHERE created_at < DATE_SUB(NOW(), INTERVAL 30 DAY);
    
    DELETE FROM amazon_sessions 
    WHERE expires_at < NOW() AND is_valid = FALSE;
END //
DELIMITER ;

-- Crear evento para ejecutar limpieza diaria
CREATE EVENT IF NOT EXISTS daily_cleanup
ON SCHEDULE EVERY 1 DAY
STARTS CURRENT_TIMESTAMP
DO CALL cleanup_old_logs();

-- Permisos para usuario de API
-- Crear usuario si no existe
CREATE USER IF NOT EXISTS 'leviatan_api'@'localhost' IDENTIFIED BY 'secure_password_here';

-- Otorgar permisos
GRANT SELECT, INSERT, UPDATE ON leviatan_chk.* TO 'leviatan_api'@'localhost';
GRANT EXECUTE ON leviatan_chk.* TO 'leviatan_api'@'localhost';

-- Aplicar cambios
FLUSH PRIVILEGES;

-- Mostrar información de la base de datos
SELECT '✅ Base de datos Leviatan CHK creada correctamente' as status;
SHOW TABLES;
